"""Gateway configuration from the environment."""
import os
from collections.abc import Callable
from typing import Literal

from src.llm.gateway import DEFAULT_EMBED_MODEL, LLMGateway, chat_client_factory
from src.llm.types import CallRecorder, Schema

# 3.1 is the fallback: when 3.5 answers 503 the next attempt goes to 3.1.
DEFAULT_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
# Chat: best first; a model out of its daily quota is skipped until it resets,
# the flash-lite pair is the last resort.
DEFAULT_CHAT_MODELS = (
    "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,"
    "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
)
# A chat answer waits for the user: an overloaded model is dropped after 30 s
# instead of the 90 s background jobs can afford.
CHAT_TIMEOUT_S = 30


class LLMConfig(Schema):
    models: tuple[str, ...]
    mode: Literal["free", "fast"]
    rpm: int
    max_attempts: int
    embed_model: str


def _models(var: str, default: str) -> tuple[str, ...]:
    return tuple(m.strip() for m in os.getenv(var, default).split(",") if m.strip())


def load_llm_config() -> LLMConfig:
    models = _models("GEMINI_MODELS", DEFAULT_MODELS)
    return LLMConfig.model_validate(
        {
            "models": models,
            "mode": os.getenv("LLM_MODE", "free"),
            "rpm": os.getenv("FREE_TIER_RPM", "15"),
            "max_attempts": os.getenv("LLM_MAX_ATTEMPTS", "8"),
            "embed_model": os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL),
        }
    )


def build_gateway(
    recorder: CallRecorder | None = None,
    mode_source: Callable[[], Literal["free", "fast"]] | None = None,
) -> LLMGateway:
    cfg = load_llm_config()
    return LLMGateway(
        cfg.models,
        mode=cfg.mode,
        rpm=cfg.rpm,
        max_attempts=cfg.max_attempts,
        embed_model=cfg.embed_model,
        mode_source=mode_source,
        recorder=recorder,
    )


def load_chat_config() -> LLMConfig:
    """Same knobs as the main gateway, with the chat's model chain."""
    return load_llm_config().model_copy(
        update={"models": _models("CHAT_MODELS", DEFAULT_CHAT_MODELS)}
    )


def build_chat_gateway(
    recorder: CallRecorder | None = None,
    mode_source: Callable[[], Literal["free", "fast"]] | None = None,
) -> LLMGateway:
    """The chat's own gateway: its own pacing and quota memory, so a busy
    triage never delays an answer and vice versa."""
    cfg = load_chat_config()
    return LLMGateway(
        cfg.models,
        mode=cfg.mode,
        rpm=cfg.rpm,
        max_attempts=cfg.max_attempts,
        embed_model=cfg.embed_model,
        mode_source=mode_source,
        recorder=recorder,
        chat_factory=chat_client_factory(CHAT_TIMEOUT_S),
    )
