from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Job
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, ExecuteActionJob, JobKind, ProcessEmailJob
from src.jobs.worker import Handler, Worker


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def jobs(session_factory: sessionmaker[Session], clock: Clock) -> JobStore:
    return JobStore(session_factory, clock=clock.now, max_attempts=5)


def _naive(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.replace(tzinfo=None)


def test_enqueue_is_idempotent(jobs: JobStore, session_factory: sessionmaker[Session]) -> None:
    assert jobs.enqueue(ProcessEmailJob(email_id="a")) is True
    assert jobs.enqueue(ProcessEmailJob(email_id="a")) is False
    with session_factory() as s:
        keys = list(s.scalars(select(Job.idempotency_key)))
    assert keys == ["process_email:a"]


def test_payload_roundtrip_is_typed(jobs: JobStore) -> None:
    jobs.enqueue(ExecuteActionJob(action_id=7))
    claimed = jobs.claim("w1")
    assert claimed is not None
    assert claimed.payload == ExecuteActionJob(action_id=7) and claimed.attempts == 1


def test_claim_orders_by_priority_then_age(jobs: JobStore) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="old"))
    jobs.enqueue(ProcessEmailJob(email_id="new"))
    jobs.enqueue(ExecuteActionJob(action_id=1))
    order = []
    while (c := jobs.claim("w1")) is not None:
        order.append(c.payload)
    assert order == [
        ProcessEmailJob(email_id="old"),
        ProcessEmailJob(email_id="new"),
        ExecuteActionJob(action_id=1),
    ]


def test_claim_skips_running_and_future_jobs(jobs: JobStore, clock: Clock) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    first = jobs.claim("w1")
    assert first is not None and jobs.claim("w2") is None
    assert jobs.fail(first.id, "boom") == "retry"
    assert jobs.claim("w1") is None  # backing off
    clock.advance(121)
    again = jobs.claim("w1")
    assert again is not None and again.attempts == 2


def test_fail_backs_off_then_dead(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    jobs = JobStore(session_factory, clock=clock.now, max_attempts=2)
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    c1 = jobs.claim("w1")
    assert c1 is not None and jobs.fail(c1.id, "e1") == "retry"
    with session_factory() as s:
        row = s.scalars(select(Job)).one()
    assert _naive(row.run_after) == _naive(clock.now() + timedelta(seconds=120))
    assert row.status == "queued" and row.last_error == "e1"
    clock.advance(120)
    c2 = jobs.claim("w1")
    assert c2 is not None and jobs.fail(c2.id, "e2") == "dead"
    assert jobs.counts().dead == 1


def test_complete_marks_done(jobs: JobStore) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    c = jobs.claim("w1")
    assert c is not None
    jobs.complete(c.id)
    counts = jobs.counts()
    assert (counts.queued, counts.running, counts.done) == (0, 0, 1)


def test_requeue_stale_returns_running_jobs_to_queue(jobs: JobStore, clock: Clock) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    assert jobs.claim("w1") is not None
    clock.advance(20 * 60)
    assert jobs.requeue_stale(timedelta(minutes=15)) == 1
    again = jobs.claim("w2")
    assert again is not None and again.attempts == 2


def test_requeue_stale_keeps_fresh_jobs(jobs: JobStore, clock: Clock) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    assert jobs.claim("w1") is not None
    clock.advance(5 * 60)
    assert jobs.requeue_stale(timedelta(minutes=15)) == 0


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.seen: list[ClaimedJob] = []
        self.fail = fail

    def __call__(self, job: ClaimedJob) -> None:
        self.seen.append(job)
        if self.fail:
            raise RuntimeError("handler boom")


def _worker(
    jobs: JobStore, handlers: dict[JobKind, Handler], dead: list[tuple[int, str]]
) -> Worker:
    return Worker(
        jobs,
        handlers,
        on_dead=lambda job, err: dead.append((job.id, err)),
        poll_s=0,
        sleep=lambda _: None,
        worker_id="w1",
        heartbeat=None,
    )


def test_worker_runs_handler_and_completes(jobs: JobStore) -> None:
    handler = Recorder()
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    w = _worker(jobs, {"process_email": handler}, [])
    assert w.run_once() is True and w.run_once() is False
    assert handler.seen[0].payload == ProcessEmailJob(email_id="a")
    assert jobs.counts().done == 1


def test_handler_exception_marks_retry_and_worker_continues(jobs: JobStore) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    jobs.enqueue(ExecuteActionJob(action_id=1))
    ok = Recorder()
    w = _worker(jobs, {"process_email": Recorder(fail=True), "execute_action": ok}, [])
    assert w.run_once() and w.run_once()
    assert len(ok.seen) == 1
    counts = jobs.counts()
    assert (counts.queued, counts.done) == (1, 1)


def test_worker_dead_job_calls_on_dead_once(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    jobs = JobStore(session_factory, clock=clock.now, max_attempts=1)
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    dead: list[tuple[int, str]] = []
    w = _worker(jobs, {"process_email": Recorder(fail=True)}, dead)
    w.run_once()
    assert len(dead) == 1 and "handler boom" in dead[0][1]


def test_worker_without_handler_fails_job(jobs: JobStore) -> None:
    jobs.enqueue(ExecuteActionJob(action_id=1))
    w = _worker(jobs, {}, [])
    assert w.run_once() is True
    with jobs.sf() as s:
        row = s.scalars(select(Job)).one()
    assert row.status == "queued" and "sem handler" in (row.last_error or "")
