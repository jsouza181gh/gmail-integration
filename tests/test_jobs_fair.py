"""Fair queue: accounts take turns inside a lane."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.db.scope import account_scope
from src.jobs.store import JobStore
from src.jobs.types import (
    AnyJob,
    BackfillPageJob,
    ExecuteActionJob,
    IndexEmailJob,
    JobKind,
    ProcessEmailJob,
)
from tests.conftest import add_account

BACKFILL: frozenset[JobKind] = frozenset({"backfill_page", "backfill_attachment"})


@pytest.fixture
def jobs(session_factory: sessionmaker[Session]) -> JobStore:
    add_account(session_factory, 2, "b@example.com")
    add_account(session_factory, 3, "c@example.com")
    return JobStore(session_factory)


def put(jobs: JobStore, account: int, *payloads: AnyJob) -> None:
    with account_scope(account):
        for p in payloads:
            assert jobs.enqueue(p)


def drain(jobs: JobStore, n: int, **lane: object) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for _ in range(n):
        c = jobs.claim("w", **lane)  # type: ignore[arg-type]
        if c is None:
            break
        out.append((c.account_id, c.payload.kind))
    return out


def test_a_big_queue_does_not_starve_another_account(jobs: JobStore) -> None:
    put(jobs, 1, *(IndexEmailJob(email_id=f"a{i}") for i in range(50)))
    put(jobs, 2, IndexEmailJob(email_id="b0"))
    accounts = [a for a, _ in drain(jobs, 2)]
    assert sorted(accounts) == [1, 2]  # B is served second at the latest


def test_accounts_alternate_while_both_have_work(jobs: JobStore) -> None:
    put(jobs, 1, *(IndexEmailJob(email_id=f"a{i}") for i in range(4)))
    put(jobs, 2, *(IndexEmailJob(email_id=f"b{i}") for i in range(4)))
    put(jobs, 3, *(IndexEmailJob(email_id=f"c{i}") for i in range(4)))
    accounts = [a for a, _ in drain(jobs, 9)]
    assert accounts == [1, 2, 3] * 3


def test_oldest_job_first_within_an_account(jobs: JobStore) -> None:
    put(jobs, 1, IndexEmailJob(email_id="old"), IndexEmailJob(email_id="new"))
    first = jobs.claim("w")
    assert first is not None and first.payload == IndexEmailJob(email_id="old")


def test_priority_still_wins_over_the_turn(jobs: JobStore) -> None:
    put(jobs, 1, IndexEmailJob(email_id="x"))
    put(jobs, 2, ExecuteActionJob(action_id=1))
    assert drain(jobs, 2) == [(2, "execute_action"), (1, "index_email")]


def test_big_backfill_in_a_never_delays_new_mail_of_b(jobs: JobStore) -> None:
    """The claim-order test from the spec, un-laned (worst case) and per lane."""
    put(jobs, 1, *(BackfillPageJob(run_id=1, page_no=i) for i in range(200)))
    put(jobs, 2, ProcessEmailJob(email_id="new"))
    assert drain(jobs, 1) == [(2, "process_email")]


def test_lanes_keep_their_own_turn(jobs: JobStore) -> None:
    put(jobs, 1, *(BackfillPageJob(run_id=1, page_no=i) for i in range(3)))
    put(jobs, 2, *(BackfillPageJob(run_id=2, page_no=i) for i in range(3)))
    put(jobs, 1, *(IndexEmailJob(email_id=f"a{i}") for i in range(2)))
    app_lane = [a for a, _ in drain(jobs, 2, exclude=BACKFILL)]
    backfill_lane = [a for a, _ in drain(jobs, 4, kinds=BACKFILL)]
    assert app_lane == [1, 1]
    assert backfill_lane == [1, 2, 1, 2]


def test_account_with_only_future_jobs_does_not_take_a_turn(
    jobs: JobStore, session_factory: sessionmaker[Session]
) -> None:
    put(jobs, 1, IndexEmailJob(email_id="a"))
    put(jobs, 2, IndexEmailJob(email_id="b"))
    first = jobs.claim("w")
    assert first is not None and first.account_id == 1
    jobs.defer(first.id, 3600, "sem chave")
    assert [a for a, _ in drain(jobs, 3)] == [2]
