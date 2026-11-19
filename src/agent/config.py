"""Agent configuration versions: immutable rows, exactly one active."""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import Channel, audit
from src.db.models import AgentConfig
from src.db.rows import AgentConfigRow

MAX_TURNS_RANGE = range(1, 9)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DbAgentConfigs:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def active(self) -> AgentConfigRow | None:
        with self.sf() as s:
            row = s.scalars(
                select(AgentConfig).where(AgentConfig.active).order_by(AgentConfig.version.desc())
            ).first()
            return AgentConfigRow.model_validate(row) if row else None

    def versions(self) -> list[AgentConfigRow]:
        with self.sf() as s:
            rows = s.scalars(select(AgentConfig).order_by(AgentConfig.version.desc()))
            return [AgentConfigRow.model_validate(r) for r in rows]

    def create(self, decide_prompt: str, max_turns: int, channel: Channel = "ui") -> AgentConfigRow:
        if max_turns not in MAX_TURNS_RANGE:
            raise ValueError("max_turns deve ficar entre 1 e 8")
        if not decide_prompt.strip():
            raise ValueError("o prompt não pode ser vazio")
        now = self._now()
        with self.sf.begin() as s:
            before = s.scalars(select(AgentConfig.version).where(AgentConfig.active)).first()
            version = (s.scalar(select(func.max(AgentConfig.version))) or 0) + 1
            s.execute(update(AgentConfig).values(active=False))
            row = AgentConfig(version=version, active=True, decide_prompt=decide_prompt,
                              max_turns=max_turns, created_at=now)
            s.add(row)
            s.flush()
            audit(s, "agent_config", row.id, channel, f"v{before}", f"v{version}", now)
            return AgentConfigRow.model_validate(row)

    def activate(self, config_id: int, channel: Channel = "ui") -> bool:
        now = self._now()
        with self.sf.begin() as s:
            target = s.get(AgentConfig, config_id)
            if target is None:
                return False
            before = s.scalars(select(AgentConfig.version).where(AgentConfig.active)).first()
            s.execute(update(AgentConfig).values(active=False))
            target.active = True
            audit(s, "agent_config", config_id, channel, f"v{before}", f"v{target.version}", now)
            return True
