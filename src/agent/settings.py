"""Runtime switches stored in `app_settings`, and the config audit trail."""
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from src.db.models import AccountSettings, ConfigAudit
from src.db.scope import current_account

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


def _settings(s: Session) -> AccountSettings:
    """The current account's row, created on first use."""
    row = s.get(AccountSettings, current_account())
    if row is None:
        row = AccountSettings(gmail_account_id=current_account())
        s.add(row)
        s.flush()
    return row


def _read(s: Session) -> AccountSettings | None:
    return s.get(AccountSettings, current_account())


class RuntimeSettings:
    """Per-account runtime switches (`account_settings`)."""

    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def llm_mode(self) -> LlmMode | None:
        with self.sf() as s:
            row = _read(s)
        if row is None or row.llm_mode is None:
            return None
        return "fast" if row.llm_mode == "fast" else "free"

    def set_llm_mode(self, mode: LlmMode, channel: Channel = "ui") -> None:
        now = self._now()
        with self.sf.begin() as s:
            row = _settings(s)
            before = row.llm_mode
            row.llm_mode = mode
            audit(s, "settings", LLM_MODE, channel, before, mode, now)

    def backfill_paused(self) -> bool:
        with self.sf() as s:
            row = _read(s)
            return row is not None and row.backfill_paused

    def set_backfill_paused(self, paused: bool, channel: Channel = "ui") -> None:
        """Paused: the history backfill stops at its next page; new mail and chat go on."""
        now = self._now()
        with self.sf.begin() as s:
            row = _settings(s)
            before = row.backfill_paused
            row.backfill_paused = paused
            audit(s, "settings", BACKFILL_PAUSED, channel,
                  "pausado" if before else "rodando", "pausado" if paused else "rodando", now)

    def backfill_turbo(self) -> bool:
        with self.sf() as s:
            row = _read(s)
            return row is not None and row.backfill_turbo

    def set_backfill_turbo(self, on: bool, channel: Channel = "ui") -> None:
        """On: the backfill also uses the app key while it is not under pressure."""
        now = self._now()
        with self.sf.begin() as s:
            row = _settings(s)
            before = row.backfill_turbo
            row.backfill_turbo = on
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
