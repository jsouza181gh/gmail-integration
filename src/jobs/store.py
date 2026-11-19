"""Postgres job queue: idempotent enqueue, SKIP LOCKED claim, backoff, dead-letter.

Every method is its own transaction. A claim increments `attempts`, so a job
that crashes the worker still counts the attempt when it is requeued as stale.
"""
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Job
from src.jobs.types import (
    PRIORITY,
    ClaimedJob,
    ExecuteActionJob,
    JobCounts,
    ProcessEmailJob,
    idempotency_key,
)

BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
ERROR_MAX = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _payload(job: Job) -> ProcessEmailJob | ExecuteActionJob:
    if job.kind == "process_email" and job.email_id is not None:
        return ProcessEmailJob(email_id=job.email_id)
    if job.kind == "execute_action" and job.action_id is not None:
        return ExecuteActionJob(action_id=job.action_id)
    raise ValueError(f"job {job.id}: kind/payload inconsistentes ({job.kind})")


class JobStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = _utcnow,
        max_attempts: int = 5,
    ) -> None:
        self.sf = session_factory
        self._now = clock
        self.max_attempts = max_attempts

    def enqueue(self, payload: ProcessEmailJob | ExecuteActionJob) -> bool:
        """False when a job with the same idempotency key already exists."""
        now = self._now()
        row = {
            "kind": payload.kind,
            "priority": PRIORITY[payload.kind],
            "status": "queued",
            "attempts": 0,
            "max_attempts": self.max_attempts,
            "run_after": now,
            "idempotency_key": idempotency_key(payload),
            "email_id": payload.email_id if isinstance(payload, ProcessEmailJob) else None,
            "action_id": payload.action_id if isinstance(payload, ExecuteActionJob) else None,
            "created_at": now,
        }
        with self.sf.begin() as s:
            insert = postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert
            result = s.execute(insert(Job).values(row).on_conflict_do_nothing())
            return isinstance(result, CursorResult) and result.rowcount > 0

    def claim(self, worker_id: str) -> ClaimedJob | None:
        now = self._now()
        with self.sf.begin() as s:
            q = (
                select(Job)
                .where(Job.status == "queued", Job.run_after <= now)
                .order_by(Job.priority.desc(), Job.id)
                .limit(1)
            )
            if s.get_bind().dialect.name == "postgresql":
                q = q.with_for_update(skip_locked=True)
            job = s.scalars(q).first()
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.locked_at = now
            job.locked_by = worker_id
            return ClaimedJob(id=job.id, attempts=job.attempts, payload=_payload(job))

    def complete(self, job_id: int) -> None:
        with self.sf.begin() as s:
            s.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status="done", finished_at=self._now(), locked_at=None, locked_by=None)
            )

    def fail(self, job_id: int, error: str) -> Literal["retry", "dead"]:
        now = self._now()
        with self.sf.begin() as s:
            job = s.get_one(Job, job_id)
            job.last_error = error[:ERROR_MAX]
            job.locked_at = None
            job.locked_by = None
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                job.finished_at = now
                return "dead"
            delay = min(BACKOFF_BASE_S * 2 ** job.attempts, BACKOFF_MAX_S)
            job.status = "queued"
            job.run_after = now + timedelta(seconds=delay)
            return "retry"

    def requeue_stale(self, older_than: timedelta) -> int:
        """Running jobs whose worker died go back to the queue."""
        with self.sf.begin() as s:
            result = s.execute(
                update(Job)
                .where(Job.status == "running", Job.locked_at < self._now() - older_than)
                .values(status="queued", locked_at=None, locked_by=None)
            )
            return result.rowcount if isinstance(result, CursorResult) else 0

    def counts(self) -> JobCounts:
        with self.sf() as s:
            rows = s.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        return JobCounts.model_validate({status: n for status, n in rows})
