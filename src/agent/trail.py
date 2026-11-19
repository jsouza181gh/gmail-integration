"""Persistent trail of agent runs and their ordered steps.

Steps are opened before the work starts (an LLM call carries its step id)
and closed with the outcome; each write is its own transaction so a crash
leaves a readable, partial trail.
"""
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import Trigger
from src.db.models import AgentRun, AgentStep
from src.db.rows import AgentRunRow, AgentStepRow

StepKind = Literal["llm", "tool", "rule", "note"]
StepSource = Literal["planned", "model", "flow"]
RunStatus = Literal["done", "truncated", "failed"]
DETAIL_MAX = 4000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Trail:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def start_run(self, trigger: Trigger, email_id: str | None, config_id: int) -> int:
        with self.sf.begin() as s:
            run = AgentRun(
                trigger=trigger,
                email_id=email_id,
                config_id=config_id,
                status="running",
                started_at=self._now(),
            )
            s.add(run)
            s.flush()
            return run.id

    def finish_run(
        self, run_id: int, status: RunStatus, *, summary: str | None, error: str | None
    ) -> None:
        with self.sf.begin() as s:
            run = s.get_one(AgentRun, run_id)
            run.status = status
            run.summary = summary
            run.error = error[:DETAIL_MAX] if error else None
            run.finished_at = self._now()

    def open_step(self, run_id: int, kind: StepKind, name: str, source: StepSource) -> int:
        with self.sf.begin() as s:
            position = s.scalar(
                select(func.count()).select_from(AgentStep).where(AgentStep.run_id == run_id)
            )
            step = AgentStep(
                run_id=run_id,
                position=position or 0,
                kind=kind,
                name=name,
                source=source,
                status="running",
                started_at=self._now(),
            )
            s.add(step)
            s.flush()
            return step.id

    def close_step(self, step_id: int, status: Literal["ok", "error"], detail: str) -> None:
        with self.sf.begin() as s:
            step = s.get_one(AgentStep, step_id)
            step.status = status
            step.detail = detail[:DETAIL_MAX]
            step.finished_at = self._now()

    def note(self, run_id: int, name: str, detail: str) -> None:
        """A step that is only a record (e.g. why the flow skipped something)."""
        self.close_step(self.open_step(run_id, "note", name, "flow"), "ok", detail)

    def run(self, run_id: int) -> AgentRunRow:
        with self.sf() as s:
            return AgentRunRow.model_validate(s.get_one(AgentRun, run_id))

    def steps(self, run_id: int) -> tuple[AgentStepRow, ...]:
        with self.sf() as s:
            rows = s.scalars(
                select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.position)
            )
            return tuple(AgentStepRow.model_validate(r) for r in rows)
