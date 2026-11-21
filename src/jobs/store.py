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
    AnyJob,
    BackfillAttachmentJob,
    BackfillPageJob,
    ChatTurnJob,
    ClaimedJob,
    DeadJob,
    ExecuteActionJob,
    IndexAttachmentJob,
    IndexEmailJob,
    JobCounts,
    JobKind,
    ProcessEmailJob,
    idempotency_key,
)

BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
ERROR_MAX = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _payload(job: Job) -> AnyJob:
    if job.kind == "chat_turn" and job.chat_message_id is not None:
        return ChatTurnJob(message_id=job.chat_message_id)
    if job.kind == "process_email" and job.email_id is not None:
        return ProcessEmailJob(email_id=job.email_id)
    if job.kind == "execute_action" and job.action_id is not None:
        return ExecuteActionJob(action_id=job.action_id)
    if job.kind == "index_email" and job.email_id is not None:
        return IndexEmailJob(email_id=job.email_id)
    if job.kind == "index_attachment" and job.attachment_id is not None:
        return IndexAttachmentJob(attachment_id=job.attachment_id)
    if job.kind == "backfill_attachment" and job.attachment_id is not None:
        return BackfillAttachmentJob(attachment_id=job.attachment_id)
    if job.kind == "backfill_page" and job.run_id is not None and job.page_no is not None:
        return BackfillPageJob(run_id=job.run_id, page_no=job.page_no)
    raise ValueError(f"job {job.id}: kind/payload inconsistentes ({job.kind})")


def _columns(payload: AnyJob) -> dict[str, str | int | None]:
    """The typed payload columns of one job row."""
    cols: dict[str, str | int | None] = {
        "email_id": None, "action_id": None, "attachment_id": None, "run_id": None, "page_no": None,
        "chat_message_id": None,
    }
    match payload:
        case ChatTurnJob(message_id=m):
            cols["chat_message_id"] = m
        case ProcessEmailJob(email_id=e) | IndexEmailJob(email_id=e):
            cols["email_id"] = e
        case ExecuteActionJob(action_id=a):
            cols["action_id"] = a
        case IndexAttachmentJob(attachment_id=a) | BackfillAttachmentJob(attachment_id=a):
            cols["attachment_id"] = a
        case BackfillPageJob(run_id=r, page_no=n):
            cols["run_id"], cols["page_no"] = r, n
    return cols


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

    def enqueue(self, payload: AnyJob) -> bool:
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
            **_columns(payload),
            "created_at": now,
        }
        with self.sf.begin() as s:
            pg = s.get_bind().dialect.name == "postgresql"
            insert = postgresql.insert if pg else sqlite.insert
            # rowcount is unreliable for ON CONFLICT DO NOTHING under psycopg;
            # RETURNING yields a row only when the insert happened.
            stmt = insert(Job).values(row).on_conflict_do_nothing().returning(Job.id)
            return s.execute(stmt).scalar_one_or_none() is not None

    def claim(
        self,
        worker_id: str,
        *,
        kinds: frozenset[JobKind] | None = None,
        exclude: frozenset[JobKind] = frozenset(),
    ) -> ClaimedJob | None:
        """Next ready job; `kinds`/`exclude` split the queue between worker lanes."""
        now = self._now()
        with self.sf.begin() as s:
            q = (
                select(Job)
                .where(Job.status == "queued", Job.run_after <= now)
                .order_by(Job.priority.desc(), Job.id)
                .limit(1)
            )
            if kinds is not None:
                q = q.where(Job.kind.in_(sorted(kinds)))
            if exclude:
                q = q.where(Job.kind.not_in(sorted(exclude)))
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

    def defer(self, job_id: int, seconds: int, reason: str) -> None:
        """Back to the queue later without charging the attempt (e.g. quota)."""
        with self.sf.begin() as s:
            job = s.get_one(Job, job_id)
            job.status = "queued"
            job.attempts = max(0, job.attempts - 1)
            job.run_after = self._now() + timedelta(seconds=seconds)
            job.last_error = reason[:ERROR_MAX]
            job.locked_at = None
            job.locked_by = None

    def requeue_orphans(self) -> int:
        """Every running job goes back, attempt refunded.

        Called once at worker start-up: there is one worker per database, so
        a job still marked running was interrupted (deploy, crash), not slow.
        """
        with self.sf.begin() as s:
            result = s.execute(
                update(Job)
                .where(Job.status == "running")
                .values(
                    status="queued",
                    attempts=func.max(Job.attempts - 1, 0)
                    if s.get_bind().dialect.name == "sqlite"
                    else func.greatest(Job.attempts - 1, 0),
                    locked_at=None,
                    locked_by=None,
                )
            )
            return result.rowcount if isinstance(result, CursorResult) else 0

    def requeue(self, job_id: int) -> bool:
        """A dead job gets a fresh set of attempts; False if it was not dead."""
        with self.sf.begin() as s:
            result = s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "dead")
                .values(status="queued", attempts=0, run_after=self._now(), finished_at=None)
            )
            return bool(result.rowcount) if isinstance(result, CursorResult) else False

    def dead(self, limit: int, kind: str | None = None) -> list[DeadJob]:
        q = select(Job).where(Job.status == "dead").order_by(Job.id.desc()).limit(limit)
        if kind is not None:
            q = q.where(Job.kind == kind)
        with self.sf() as s:
            return [
                DeadJob(
                    id=j.id, kind=j.kind, attempts=j.attempts,
                    last_error=j.last_error, finished_at=j.finished_at,
                )
                for j in s.scalars(q)
            ]

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

    def counts_by_kind(self) -> dict[str, JobCounts]:
        with self.sf() as s:
            rows = s.execute(
                select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
            ).all()
        grouped: dict[str, dict[str, int]] = {}
        for kind, status, n in rows:
            grouped.setdefault(kind, {})[status] = n
        return {k: JobCounts.model_validate(v) for k, v in sorted(grouped.items())}
