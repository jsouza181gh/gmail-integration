"""A wait longer than the inline limit goes back to the queue, never sleeps in the lane."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Job
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, ProcessEmailJob
from src.jobs.worker import Worker
from src.llm.errors import LLMExhaustedError, LLMWaitError
from src.llm.gateway import LLMGateway
from tests.test_llm_gateway import FakeChat, FakeClock, ListRecorder, Out, P, Wall


def limited(chat: FakeChat, clock: FakeClock, *, mode: str = "free", limit: float = 15) -> LLMGateway:
    return LLMGateway(
        ("m1",), mode="free" if mode == "free" else "fast", rpm=15, max_attempts=8,
        recorder=ListRecorder(), chat_factory={"m1": chat}.__getitem__, max_wait_s=limit,
        sleep=clock.sleep, clock=clock.now, wall=Wall().now,
    )


def test_short_pacing_still_sleeps_inline() -> None:
    clock = FakeClock()
    g = limited(FakeChat([Out(x=1), Out(x=2)]), clock)
    g.generate_structured(P, Out)
    g.generate_structured(P, Out)
    assert clock.slept == [4.0]


def test_long_pacing_is_handed_back_without_sleeping() -> None:
    clock = FakeClock()
    g = limited(FakeChat([Out(x=1), Out(x=2)]), clock, limit=2)
    g.generate_structured(P, Out)
    with pytest.raises(LLMWaitError) as err:
        g.generate_structured(P, Out)
    assert clock.slept == [] and err.value.seconds >= 3
    # the refused call did not reserve the slot: later it is free again
    clock.t += 10
    assert g.generate_structured(P, Out).parsed == Out(x=2)


def test_long_rate_limit_backoff_is_handed_back() -> None:
    clock = FakeClock()
    chat = FakeChat([RuntimeError("429 retry in 40s"), Out(x=1)])
    g = limited(chat, clock, mode="fast")
    with pytest.raises(LLMWaitError) as err:
        g.generate_structured(P, Out)
    assert clock.slept == [] and 41 <= err.value.seconds <= 44
    assert isinstance(err.value, LLMExhaustedError) and err.value.kinds == ("rate",)


def test_without_a_limit_the_gateway_sleeps_as_before() -> None:
    clock = FakeClock()
    g = LLMGateway(
        ("m1",), mode="fast", chat_factory={"m1": FakeChat([RuntimeError("429 retry in 40s"),
                                                            Out(x=1)])}.__getitem__,
        sleep=clock.sleep, clock=clock.now, wall=Wall().now,
    )
    assert g.generate_structured(P, Out).parsed == Out(x=1)
    assert clock.slept and clock.slept[0] >= 41


def test_worker_defers_a_waiting_job_without_spending_an_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    jobs = JobStore(session_factory, max_attempts=5)
    jobs.enqueue(ProcessEmailJob(email_id="a"))

    def handler(job: ClaimedJob) -> None:
        raise LLMWaitError(90, "429")

    dead: list[str] = []
    worker = Worker(jobs, {"process_email": handler}, on_dead=lambda j, e: dead.append(e),
                    poll_s=0, worker_id="w", heartbeat=None)
    assert worker.run_once() is True
    with session_factory() as s:
        job = s.query(Job).one()
    assert job.status == "queued" and job.attempts == 0 and not dead
