import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env")


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    primary_model: str = os.getenv("PRIMARY_LLM_MODEL", "gpt-4o")
    fallback_model: str = os.getenv("FALLBACK_LLM_MODEL", "gpt-3.5-turbo")
    max_input_tokens: int = int(os.getenv("MAX_INPUT_TOKENS", "1000"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "300"))


@dataclass(frozen=True)
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", "6379"))
    db: int = int(os.getenv("REDIS_DB", "0"))
    ttl_seconds: int = int(os.getenv("REDIS_TTL_SECONDS", "3600"))


llm_config = LLMConfig()
redis_config = RedisConfig()
