"""Active agent configuration version."""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import AgentConfig
from src.db.rows import AgentConfigRow


class DbAgentConfigs:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def active(self) -> AgentConfigRow | None:
        with self.sf() as s:
            row = s.scalars(
                select(AgentConfig).where(AgentConfig.active).order_by(AgentConfig.version.desc())
            ).first()
            return AgentConfigRow.model_validate(row) if row else None
