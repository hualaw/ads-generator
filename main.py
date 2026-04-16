import json
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import llm_config
from prompts import SYSTEM_PROMPT, USER_PROMPT
from utils.log import log_event, log_exception
from utils.llm import (
    get_cached_llm_output,
    set_cached_llm_output,
    output_parse,
    truncate_to_max_tokens,
    stream_llm_chunks,
)

app = FastAPI(title="Ads Generator API")


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
