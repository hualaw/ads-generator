import hashlib
import json
import logging
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from openai import APITimeoutError, AsyncOpenAI
import redis
import tiktoken

from config import llm_config, redis_config
from prompts import SYSTEM_PROMPT, USER_PROMPT


app = FastAPI(title="Ads Generator API")

logger = logging.getLogger("ads_generator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

openai_client = AsyncOpenAI(api_key=llm_config.api_key) if llm_config.api_key else None

redis_client = redis.Redis(
    host=redis_config.host,
    port=redis_config.port,
    db=redis_config.db,
    decode_responses=True,
)


def log_event(level: str, event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    message = json.dumps(payload, ensure_ascii=True, default=str)
    if level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)


def log_exception(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    message = json.dumps(payload, ensure_ascii=True, default=str)
    logger.exception(message)


def _get_cache_key(prompt: str, model: str) -> str:
    """Generate cache key from prompt and model name."""
    combined = f"{prompt}::{model}"
    return hashlib.sha256(combined.encode()).hexdigest()


def get_cached_llm_output(prompt: str, model: str) -> str | None:
    """Retrieve LLM output from Redis cache."""
    try:
        cache_key = _get_cache_key(prompt, model)
        cached = redis_client.get(cache_key)
        return cached if isinstance(cached, str) else None
    except Exception:
        log_exception("redis_cache_get_failed", model=model)
        return None


def set_cached_llm_output(prompt: str, model: str, output: str) -> None:
    """Store LLM output to Redis cache."""
    try:
        cache_key = _get_cache_key(prompt, model)
        redis_client.setex(cache_key, redis_config.ttl_seconds, output)
    except Exception:
        log_exception("redis_cache_set_failed", model=model, ttl_seconds=redis_config.ttl_seconds)


def output_parse(llm_output: str, num_ads: int) -> list[str]:
    """Parse LLM JSON output and extract ads list."""
    try:
        result = json.loads(llm_output)
        if not isinstance(result.get("ads"), list):
            raise ValueError("Expected 'ads' field to be a list")
        ads = result["ads"]
    except (json.JSONDecodeError, ValueError) as e:
        log_exception(
            "llm_output_parse_failed",
            num_ads=num_ads,
            output_preview=llm_output[:300],
            reason=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"LLM output format is invalid: {str(e)}. Expected JSON with 'ads' field.",
        )
    
    if len(ads) < num_ads:
        log_event(
            "error",
            "llm_output_ads_count_insufficient",
            expected_ads=num_ads,
            actual_ads=len(ads),
        )
        raise HTTPException(
            status_code=500,
            detail=f"LLM returned {len(ads)} ads but {num_ads} were requested.",
        )
    
    return ads


def truncate_to_max_tokens(text: str, max_tokens: int, model: str) -> str:
    """Truncate text to max token count using the target model encoding."""
    if max_tokens <= 0:
        log_event("error", "invalid_max_input_tokens", max_input_tokens=max_tokens)
        return ""

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        log_event("warning", "token_encoding_fallback", model=model, fallback_encoding="cl100k_base")
        encoding = tiktoken.get_encoding("cl100k_base")

    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text

    log_event(
        "warning",
        "prompt_truncated",
        model=model,
        original_token_count=len(tokens),
        max_input_tokens=max_tokens,
    )

    return encoding.decode(tokens[:max_tokens])


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    if openai_client is None:
        log_event("error", "openai_client_not_configured")
        raise ValueError("OPENAI_API_KEY is not configured.")

    try:
        response = await openai_client.chat.completions.create(
            model=llm_config.primary_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=llm_config.max_output_tokens,
            timeout=llm_config.timeout_seconds,
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except APITimeoutError:
        log_exception(
            "llm_primary_timeout",
            primary_model=llm_config.primary_model,
            timeout_seconds=llm_config.timeout_seconds,
            fallback_model=llm_config.fallback_model,
        )
        try:
            fallback_response = await openai_client.chat.completions.create(
                model=llm_config.fallback_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=llm_config.max_output_tokens,
                timeout=llm_config.timeout_seconds,
            )
            content = fallback_response.choices[0].message.content
            log_event("warning", "llm_fallback_succeeded", fallback_model=llm_config.fallback_model)
            return content.strip() if content else ""
        except Exception:
            log_exception("llm_fallback_failed", fallback_model=llm_config.fallback_model)
            raise HTTPException(status_code=502, detail="LLM request failed on primary and fallback models.")
    except Exception:
        log_exception("llm_primary_failed", primary_model=llm_config.primary_model)
        raise HTTPException(status_code=502, detail="LLM request failed.")


async def stream_llm_chunks(system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
    if openai_client is None:
        log_event("error", "openai_client_not_configured")
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    try:
        stream = await openai_client.chat.completions.create(
            model=llm_config.primary_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=llm_config.max_output_tokens,
            timeout=llm_config.timeout_seconds,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
    except APITimeoutError:
        log_exception(
            "llm_primary_timeout_stream",
            primary_model=llm_config.primary_model,
            timeout_seconds=llm_config.timeout_seconds,
            fallback_model=llm_config.fallback_model,
        )
        try:
            fallback_stream = await openai_client.chat.completions.create(
                model=llm_config.fallback_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=llm_config.max_output_tokens,
                timeout=llm_config.timeout_seconds,
                stream=True,
            )
            log_event("warning", "llm_fallback_stream_started", fallback_model=llm_config.fallback_model)
            async for chunk in fallback_stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception:
            log_exception("llm_fallback_stream_failed", fallback_model=llm_config.fallback_model)
            raise HTTPException(status_code=502, detail="LLM stream failed on primary and fallback models.")
    except Exception:
        log_exception("llm_primary_stream_failed", primary_model=llm_config.primary_model)
        raise HTTPException(status_code=502, detail="LLM stream failed.")


class GenerateAdsRequest(BaseModel):
    product: str = Field(..., description="Product name")
    audience: str = Field(..., description="Target audience")
    num_ads: int = Field(..., gt=0, description="Number of ads to generate")


def _to_sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


@app.post("/generate_ads")
async def generate_ads(request: GenerateAdsRequest) -> StreamingResponse:
    raw_user_prompt = USER_PROMPT.format(
        num_ads=request.num_ads,
        product=request.product,
        audience=request.audience,
    )
    user_prompt = truncate_to_max_tokens(
        raw_user_prompt,
        llm_config.max_input_tokens,
        llm_config.primary_model,
    )

    async def event_stream() -> AsyncIterator[str]:
        cached_output = get_cached_llm_output(user_prompt, llm_config.primary_model)
        if cached_output:
            try:
                ads = output_parse(cached_output, request.num_ads)
                log_event("info", "generate_ads_cache_hit_stream", model=llm_config.primary_model)
                yield _to_sse("meta", {"source": "cache"})
                yield _to_sse("complete", {"ads": ads[: request.num_ads]})
                return
            except HTTPException:
                log_event("warning", "generate_ads_cache_invalid_stream", model=llm_config.primary_model)

        llm_output_chunks: list[str] = []
        yield _to_sse("meta", {"source": "llm"})
        try:
            async for chunk in stream_llm_chunks(SYSTEM_PROMPT, user_prompt):
                llm_output_chunks.append(chunk)
                yield _to_sse("delta", {"content": chunk})

            llm_output = "".join(llm_output_chunks).strip()
            ads = output_parse(llm_output, request.num_ads)
            set_cached_llm_output(user_prompt, llm_config.primary_model, llm_output)
            yield _to_sse("complete", {"ads": ads[: request.num_ads]})
        except HTTPException as e:
            log_event("error", "generate_ads_stream_failed", status_code=e.status_code, detail=e.detail)
            yield _to_sse("error", {"status_code": e.status_code, "detail": e.detail})
        except Exception:
            log_exception("generate_ads_stream_unhandled")
            yield _to_sse("error", {"status_code": 500, "detail": "Unexpected server error."})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
