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


# --- indexing/backfill kinds, deferral, orphans, dead requeue -----------------

def test_new_kinds_roundtrip_and_priority_order(jobs: JobStore) -> None:
    from src.jobs.types import BackfillPageJob, IndexAttachmentJob, IndexEmailJob

    jobs.enqueue(BackfillPageJob(run_id=1, page_no=0))
    jobs.enqueue(IndexAttachmentJob(attachment_id=3))
    jobs.enqueue(IndexEmailJob(email_id="e"))
    jobs.enqueue(ProcessEmailJob(email_id="p"))
    order = []
    while (c := jobs.claim("w1")) is not None:
        order.append(c.payload)
    assert order == [
        ProcessEmailJob(email_id="p"),
        IndexEmailJob(email_id="e"),
        IndexAttachmentJob(attachment_id=3),
        BackfillPageJob(run_id=1, page_no=0),
    ]


def test_backfill_page_idempotent_per_page(jobs: JobStore) -> None:
    from src.jobs.types import BackfillPageJob

    assert jobs.enqueue(BackfillPageJob(run_id=1, page_no=0)) is True
    assert jobs.enqueue(BackfillPageJob(run_id=1, page_no=0)) is False
    assert jobs.enqueue(BackfillPageJob(run_id=1, page_no=1)) is True


def test_deferred_refunds_attempt_and_delays(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    from src.jobs.worker import Deferred

    jobs = JobStore(session_factory, clock=clock.now, max_attempts=1)
    jobs.enqueue(ProcessEmailJob(email_id="a"))

    def defer(job: ClaimedJob) -> None:
        raise Deferred(1800, "cota esgotada")

    dead: list[tuple[int, str]] = []
    w = Worker(jobs, {"process_email": defer}, on_dead=lambda j, e: dead.append((j.id, e)),
               poll_s=0, sleep=lambda _: None, worker_id="w1", heartbeat=None)
    assert w.run_once() is True
    with session_factory() as s:
        row = s.scalars(select(Job)).one()
    assert (row.status, row.attempts, dead) == ("queued", 0, [])
    assert _naive(row.run_after) == _naive(clock.now() + timedelta(seconds=1800))
    assert "cota esgotada" in (row.last_error or "")
    assert jobs.claim("w1") is None
    clock.advance(1801)
    assert jobs.claim("w1") is not None


def test_startup_requeues_orphans_without_charge(jobs: JobStore) -> None:
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    assert jobs.claim("old-worker") is not None  # then the container is killed
    seen = Recorder()
    w = _worker(jobs, {"process_email": seen}, [])
    loops = iter([False, True])
    w.run_forever(stop=lambda: next(loops))
    assert len(seen.seen) == 1 and seen.seen[0].attempts == 1
    assert jobs.counts().done == 1


def test_requeue_dead_job(session_factory: sessionmaker[Session], clock: Clock) -> None:
    jobs = JobStore(session_factory, clock=clock.now, max_attempts=1)
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    job = jobs.claim("w1")
    assert job is not None and jobs.fail(job.id, "boom") == "dead"
    dead = jobs.dead(10)
    assert [(d.id, d.kind, d.last_error) for d in dead] == [(job.id, "process_email", "boom")]
    assert jobs.requeue(job.id) is True and jobs.requeue(job.id) is False
    again = jobs.claim("w1")
    assert again is not None and again.attempts == 1


def test_counts_by_kind(jobs: JobStore) -> None:
    from src.jobs.types import IndexEmailJob

    jobs.enqueue(ProcessEmailJob(email_id="a"))
    jobs.enqueue(IndexEmailJob(email_id="a"))
    jobs.enqueue(IndexEmailJob(email_id="b"))
    by_kind = jobs.counts_by_kind()
    assert by_kind["index_email"].queued == 2 and by_kind["process_email"].queued == 1


def test_cli_requeue_all_dead(session_factory: sessionmaker[Session], clock: Clock) -> None:
    from src.cli.jobs import dead, requeue, status

    jobs = JobStore(session_factory, clock=clock.now, max_attempts=1)
    for e in ("a", "b"):
        jobs.enqueue(ProcessEmailJob(email_id=e))
        job = jobs.claim("w1")
        assert job is not None
        jobs.fail(job.id, "boom")
    assert "process_email" in status(jobs) and "boom" in dead(jobs, 10, None)
    assert requeue(jobs, [], True, "process_email").startswith("2 job(s)")
    assert jobs.counts().queued == 2


def test_chat_turn_roundtrip_and_priority(jobs: JobStore) -> None:
    from src.jobs.types import ChatTurnJob

    jobs.enqueue(ProcessEmailJob(email_id="a"))
    assert jobs.enqueue(ChatTurnJob(message_id=9)) is True
    assert jobs.enqueue(ChatTurnJob(message_id=9)) is False
    first = jobs.claim("w1")
    assert first is not None and first.payload == ChatTurnJob(message_id=9)


def test_chat_lane_claims_only_chat(jobs: JobStore) -> None:
    from src.jobs.types import BackfillPageJob, ChatTurnJob

    jobs.enqueue(BackfillPageJob(run_id=1, page_no=0))
    assert jobs.claim("chat", kinds=frozenset({"chat_turn"})) is None
    jobs.enqueue(ChatTurnJob(message_id=1))
    got = jobs.claim("chat", kinds=frozenset({"chat_turn"}))
    assert got is not None and got.payload.kind == "chat_turn"


def test_main_lane_excludes_chat(jobs: JobStore) -> None:
    from src.jobs.types import ChatTurnJob

    jobs.enqueue(ChatTurnJob(message_id=1))
    jobs.enqueue(ProcessEmailJob(email_id="a"))
    got = jobs.claim("main", exclude=frozenset({"chat_turn"}))
    assert got is not None and got.payload == ProcessEmailJob(email_id="a")
    assert jobs.claim("main", exclude=frozenset({"chat_turn"})) is None


def test_non_housekeeping_lane_skips_requeue(jobs: JobStore) -> None:
    from src.jobs.types import ChatTurnJob

    jobs.enqueue(ProcessEmailJob(email_id="a"))
    assert jobs.claim("main") is not None  # running in the main lane
    seen = Recorder()
    lane = Worker(jobs, {"chat_turn": seen}, on_dead=lambda j, e: None, poll_s=0,
                  sleep=lambda _: None, worker_id="chat", heartbeat=None,
                  kinds=frozenset({"chat_turn"}), housekeeping=False)
    jobs.enqueue(ChatTurnJob(message_id=1))
    loops = iter([False, True])
    lane.run_forever(stop=lambda: next(loops))
    assert len(seen.seen) == 1
    assert jobs.counts().running == 1  # the main lane's job was left alone


def test_backfill_attachment_roundtrip_and_key(jobs: JobStore) -> None:
    from src.jobs.types import BackfillAttachmentJob, IndexAttachmentJob

    assert jobs.enqueue(BackfillAttachmentJob(attachment_id=3))
    assert not jobs.enqueue(BackfillAttachmentJob(attachment_id=3))
    assert jobs.enqueue(IndexAttachmentJob(attachment_id=3))  # other kind, other key
    first = jobs.claim("w1", kinds=frozenset({"backfill_attachment"}))
    assert first is not None and first.payload == BackfillAttachmentJob(attachment_id=3)
