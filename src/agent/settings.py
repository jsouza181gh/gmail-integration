"""Runtime switches stored in `app_settings`, and the config audit trail."""
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from src.db.models import AppSetting, ConfigAudit

LlmMode = Literal["free", "fast"]
LLM_MODE = "llm_mode"
BACKFILL_PAUSED = "backfill_paused"
BACKFILL_TURBO = "backfill_turbo"
Channel = Literal["ui", "cli", "chat"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def audit(
    s: Session, entity: str, entity_id: str | int, channel: Channel,
    before: str | None, after: str | None, now: datetime,
) -> None:
    s.add(ConfigAudit(entity=entity, entity_id=str(entity_id)[:64], channel=channel,
                      before=before, after=after, created_at=now))


def _backfill_paused(s: Session) -> bool:
    row = s.get(AppSetting, BACKFILL_PAUSED)
    return row is not None and row.value == "1"


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

    def backfill_paused(self) -> bool:
        with self.sf() as s:
            return _backfill_paused(s)

    def set_backfill_paused(self, paused: bool, channel: Channel = "ui") -> None:
        """Paused: the history backfill stops at its next page; new mail and chat go on."""
        now = self._now()
        with self.sf.begin() as s:
            before = _backfill_paused(s)
            if paused:
                s.merge(AppSetting(key=BACKFILL_PAUSED, value="1", updated_at=now))
            else:
                row = s.get(AppSetting, BACKFILL_PAUSED)
                if row is not None:
                    s.delete(row)
            audit(s, "settings", BACKFILL_PAUSED, channel,
                  "pausado" if before else "rodando", "pausado" if paused else "rodando", now)

    def backfill_turbo(self) -> bool:
        with self.sf() as s:
            row = s.get(AppSetting, BACKFILL_TURBO)
            return row is not None and row.value == "1"

    def set_backfill_turbo(self, on: bool, channel: Channel = "ui") -> None:
        """On: the backfill also uses the app key while it is not under pressure."""
        now = self._now()
        with self.sf.begin() as s:
            row = s.get(AppSetting, BACKFILL_TURBO)
            before = row is not None and row.value == "1"
            if on:
                s.merge(AppSetting(key=BACKFILL_TURBO, value="1", updated_at=now))
            elif row is not None:
                s.delete(row)
            audit(s, "settings", BACKFILL_TURBO, channel,
                  "ligado" if before else "desligado", "ligado" if on else "desligado", now)


def turbo_source(
    session_factory: sessionmaker[Session], ttl_s: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], bool]:
    """Reads backfill_turbo for the backfill's LLM, cached for ttl_s."""
    settings = RuntimeSettings(session_factory)
    cache: list[tuple[float, bool]] = []

    def read() -> bool:
        now = clock()
        if cache and now - cache[0][0] < ttl_s:
            return cache[0][1]
        value = settings.backfill_turbo()
        cache[:] = [(now, value)]
        return value

    return read


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
