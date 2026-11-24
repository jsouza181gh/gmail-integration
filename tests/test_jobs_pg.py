"""Queue behaviour that only Postgres has (row locks). Runs when
TEST_DATABASE_URL points at a migrated database (CI does this)."""
import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Job
from src.db.session import make_session_factory
from src.jobs.store import JobStore
from src.jobs.types import ProcessEmailJob

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")


@pytest.fixture
def pg() -> sessionmaker[Session]:
    sf = make_session_factory(URL)
    with sf.begin() as s:
        s.execute(text("DELETE FROM jobs"))
        s.execute(text("DELETE FROM emails WHERE id LIKE 'pgtest-%'"))
        for i in ("pgtest-a", "pgtest-b"):
            s.execute(text("INSERT INTO emails (gmail_account_id, id, attempts) VALUES (1, :i, 0)"), {"i": i})
    return sf


def test_claim_skips_rows_locked_by_another_transaction(pg: sessionmaker[Session]) -> None:
    jobs = JobStore(pg)
    jobs.enqueue(ProcessEmailJob(email_id="pgtest-a"))
    jobs.enqueue(ProcessEmailJob(email_id="pgtest-b"))
    with pg.begin() as other:
        locked = other.scalars(
            select(Job).order_by(Job.id).limit(1).with_for_update()
        ).one()
        claimed = jobs.claim("w1")  # must not block on the locked row
        assert claimed is not None and claimed.id != locked.id


def test_enqueue_conflict_reports_false_on_postgres(pg: sessionmaker[Session]) -> None:
    jobs = JobStore(pg)
    assert jobs.enqueue(ProcessEmailJob(email_id="pgtest-a")) is True
    assert jobs.enqueue(ProcessEmailJob(email_id="pgtest-a")) is False


def test_two_lanes_skip_locked(pg: sessionmaker[Session]) -> None:
    from datetime import UTC, datetime

    from src.jobs.types import ChatTurnJob

    now = datetime.now(UTC)
    with pg.begin() as s:
        s.execute(text("DELETE FROM chat_messages"))
        s.execute(text("DELETE FROM chat_conversations"))
        conv = s.execute(text(
            "INSERT INTO chat_conversations (gmail_account_id, created_at, updated_at)"
            " VALUES (1, :n, :n) RETURNING id"
        ), {"n": now}).scalar_one()
        msg = s.execute(text(
            "INSERT INTO chat_messages (gmail_account_id, conversation_id, role, text, input,"
            " status, created_at, updated_at)"
            " VALUES (1, :c, 'assistant', '', 'text', 'queued', :n, :n) RETURNING id"
        ), {"c": conv, "n": now}).scalar_one()
    jobs = JobStore(pg)
    jobs.enqueue(ProcessEmailJob(email_id="pgtest-a"))
    jobs.enqueue(ChatTurnJob(message_id=msg))
    main = jobs.claim("main", exclude=frozenset({"chat_turn"}))
    chat = jobs.claim("chat", kinds=frozenset({"chat_turn"}))
    assert main is not None and main.payload.kind == "process_email"
    assert chat is not None and chat.payload == ChatTurnJob(message_id=msg)
