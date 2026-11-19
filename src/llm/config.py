"""Gateway configuration from the environment."""
import os
from typing import Literal

from src.llm.gateway import DEFAULT_EMBED_MODEL, LLMGateway
from src.llm.types import CallRecorder, Schema

# 3.1 is the fallback: when 3.5 answers 503 the next attempt goes to 3.1.
DEFAULT_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"


class LLMConfig(Schema):
    models: tuple[str, ...]
    mode: Literal["free", "fast"]
    rpm: int
    max_attempts: int
    embed_model: str


def load_llm_config() -> LLMConfig:
    models = tuple(
        m.strip()
        for m in os.getenv("GEMINI_MODELS", DEFAULT_MODELS).split(",")
        if m.strip()
    )
    return LLMConfig.model_validate(
        {
            "models": models,
            "mode": os.getenv("LLM_MODE", "free"),
            "rpm": os.getenv("FREE_TIER_RPM", "15"),
            "max_attempts": os.getenv("LLM_MAX_ATTEMPTS", "8"),
            "embed_model": os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL),
        }
    )


def build_gateway(recorder: CallRecorder | None = None) -> LLMGateway:
    cfg = load_llm_config()
    return LLMGateway(
        cfg.models,
        mode=cfg.mode,
        rpm=cfg.rpm,
        max_attempts=cfg.max_attempts,
        embed_model=cfg.embed_model,
        recorder=recorder,
    )
