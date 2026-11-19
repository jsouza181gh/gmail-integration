"""Gateway configuration from the environment."""
import os
from typing import Literal

from src.llm.gateway import LLMGateway
from src.llm.types import CallRecorder, Schema


class LLMConfig(Schema):
    models: tuple[str, ...]
    mode: Literal["free", "fast"]
    rpm: int
    max_attempts: int


def load_llm_config() -> LLMConfig:
    models = tuple(
        m.strip()
        for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite").split(",")
        if m.strip()
    )
    return LLMConfig.model_validate(
        {
            "models": models,
            "mode": os.getenv("LLM_MODE", "free"),
            "rpm": os.getenv("FREE_TIER_RPM", "15"),
            "max_attempts": os.getenv("LLM_MAX_ATTEMPTS", "8"),
        }
    )


def build_gateway(recorder: CallRecorder | None = None) -> LLMGateway:
    cfg = load_llm_config()
    return LLMGateway(
        cfg.models,
        mode=cfg.mode,
        rpm=cfg.rpm,
        max_attempts=cfg.max_attempts,
        recorder=recorder,
    )
