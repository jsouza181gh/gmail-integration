"""Runtime switches stored in `app_settings`, and the config audit trail."""
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from src.db.models import AppSetting, ConfigAudit

LlmMode = Literal["free", "fast"]
LLM_MODE = "llm_mode"
Channel = Literal["ui", "cli", "chat"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def audit(
    s: Session, entity: str, entity_id: str | int, channel: Channel,
    before: str | None, after: str | None, now: datetime,
) -> None:
    s.add(ConfigAudit(entity=entity, entity_id=str(entity_id)[:64], channel=channel,
                      before=before, after=after, created_at=now))


class RuntimeSettings:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def llm_mode(self) -> LlmMode | None:
        with self.sf() as s:
            row = s.get(AppSetting, LLM_MODE)
        if row is None:
            return None
        return "fast" if row.value == "fast" else "free"

    def set_llm_mode(self, mode: LlmMode, channel: Channel = "ui") -> None:
        now = self._now()
        with self.sf.begin() as s:
            row = s.get(AppSetting, LLM_MODE)
            before = row.value if row else None
            s.merge(AppSetting(key=LLM_MODE, value=mode, updated_at=now))
            audit(s, "settings", LLM_MODE, channel, before, mode, now)


def mode_source(
    session_factory: sessionmaker[Session], default: LlmMode, ttl_s: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], LlmMode]:
    """Reads llm_mode for the gateway, cached for ttl_s; env default when unset."""
    settings = RuntimeSettings(session_factory)
    cache: dict[str, tuple[float, LlmMode]] = {}

    def current() -> LlmMode:
        now = clock()
        hit = cache.get(LLM_MODE)
        if hit is not None and now - hit[0] < ttl_s:
            return hit[1]
        try:
            mode = settings.llm_mode() or default
        except Exception:  # noqa: BLE001 - a DB hiccup must not stop LLM calls
            mode = hit[1] if hit else default
        cache[LLM_MODE] = (now, mode)
        return mode

    return current
