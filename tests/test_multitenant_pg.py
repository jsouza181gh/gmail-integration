"""Migration 0021 on Postgres: the single-user install becomes account 1 with
every row kept, and row-level security confines raw SQL to the session's
account. Runs when TEST_DATABASE_URL points at a Postgres (CI does this).

Each test rebuilds the schema: down to an empty `public`, up to 0020, seed,
up to head."""
import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from src.db.vector import EMBED_DIM

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida"),
    pytest.mark.noscope,
]


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Config, Engine]]:
    assert URL is not None
    monkeypatch.setenv("DATABASE_URL", URL)
    engine = create_engine(URL)
    _reset(engine)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0020")
    yield cfg, engine
    _reset(engine)  # leave a migrated database for the other Postgres tests
    command.upgrade(cfg, "head")
    engine.dispose()


def _reset(engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))


SEED = """
INSERT INTO users (id, username, password_hash, totp_secret_enc, created_at, password_changed_at)
  VALUES (1, 'joao', 'h', 't', now(), now());
INSERT INTO watch_state (id, email_address, history_id, expiration, updated_at)
  VALUES (1, 'Joao@Example.com', '4242', 99, now());
INSERT INTO app_settings (key, value, updated_at) VALUES
  ('llm_mode', 'fast', now()), ('backfill_paused', '1', now()), ('global_lock', '0', now());
INSERT INTO runs (id, query, pages, fetch_done) VALUES (1, 'q', 0, false);
INSERT INTO emails (id, subject, attempts) VALUES ('m1', 'a', 0), ('m2', 'b', 0);
INSERT INTO run_emails (run_id, email_id) VALUES (1, 'm1');
INSERT INTO email_embeddings (email_id, model, embedding) VALUES ('m1', 'm', :vec);
INSERT INTO attachments (email_id, part_id, filename, mime_type, size, status)
  VALUES ('m1', '1', 'a.pdf', 'application/pdf', 1, 'done');
INSERT INTO agent_runs (trigger, email_id, config_id, status, started_at)
  SELECT 'webhook', 'm1', min(id), 'done', now() FROM agent_configs;
INSERT INTO suggested_actions (email_id, kind, reason, status, origin, created_at)
  VALUES ('m1', 'archive', 'r', 'pending', 'agent', now());
INSERT INTO jobs (kind, priority, status, attempts, max_attempts, run_after, idempotency_key,
                  email_id, created_at)
  VALUES ('process_email', 0, 'done', 1, 5, now(), 'process_email:m1', 'm1', now());
INSERT INTO gmail_labels (name, gmail_id, updated_at) VALUES ('IA/Finanças', 'Label_1', now());
INSERT INTO chat_conversations (id, title, created_at, updated_at) VALUES (1, 't', now(), now());
INSERT INTO chat_messages (id, conversation_id, role, text, input, status, created_at, updated_at)
  VALUES (1, 1, 'assistant', 'x', 'text', 'done', now(), now());
INSERT INTO chat_message_refs (message_id, position, email_id) VALUES (1, 0, 'm2');
INSERT INTO llm_calls (id, purpose, model, attempt, status, latency_ms, input_tokens, output_tokens,
                       total_tokens, cached_input_tokens, reasoning_tokens, audio_input_tokens)
  VALUES (1, 'p', 'm', 1, 'ok', 1, 1, 1, 2, 0, 0, 0);
INSERT INTO llm_call_emails (call_id, email_id) VALUES (1, 'gone-forever');
"""


VEC = "[" + ",".join(["1"] + ["0"] * (EMBED_DIM - 1)) + "]"


def _seed(engine: Engine) -> None:
    with engine.begin() as c:
        for stmt in SEED.split(";"):
            if stmt.strip():
                c.execute(text(stmt), {"vec": VEC} if ":vec" in stmt else {})


def _counts(engine: Engine, skip: set[str]) -> dict[str, int]:
    tables = set(inspect(engine).get_table_names()) - skip - {"alembic_version"}
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in sorted(tables)}


MOVED = {"watch_state", "app_settings", "gmail_accounts", "account_settings"}


def test_upgrade_keeps_counts(pg: tuple[Config, Engine]) -> None:
    cfg, engine = pg
    _seed(engine)
    before = _counts(engine, MOVED)
    command.upgrade(cfg, "head")
    assert _counts(engine, MOVED) == before
    with engine.connect() as c:
        acc = c.execute(text("SELECT id, user_id, email_address, history_id, status"
                             " FROM gmail_accounts")).all()
        assert [tuple(r) for r in acc] == [(1, 1, "joao@example.com", "4242", "connected")]
        assert c.execute(text("SELECT role, email FROM users")).one() == ("admin", "joao@example.com")
        settings = c.execute(text("SELECT llm_mode, backfill_paused, backfill_turbo"
                                  " FROM account_settings")).one()
        assert tuple(settings) == ("fast", True, False)
        keys = c.execute(text("SELECT key FROM app_settings")).scalars().all()
        assert keys == ["global_lock"]
        owners = c.execute(text("SELECT DISTINCT gmail_account_id FROM emails")).scalars().all()
        assert owners == [1]
        # a new account gets the next id (the migration inserted id 1 explicitly)
        new_id = c.execute(text(
            "INSERT INTO gmail_accounts (user_id, email_address, status, connected_at)"
            " VALUES (2, 'b@x.y', 'connected', now()) RETURNING id")).scalar_one()
        assert new_id == 2


def test_migration_with_orphan_refs(pg: tuple[Config, Engine]) -> None:
    """llm_call_emails has no foreign key: a row naming mail that is gone stays."""
    cfg, engine = pg
    _seed(engine)
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT email_id FROM llm_call_emails")).scalars().all() == [
            "gone-forever"
        ]


def test_rls_blocks_forgotten_filter(pg: tuple[Config, Engine]) -> None:
    from src.db.models import Email
    from src.db.scope import account_scope
    from src.db.session import make_session_factory

    cfg, engine = pg
    _seed(engine)
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        c.execute(text("INSERT INTO gmail_accounts (id, user_id, email_address, status,"
                       " connected_at) VALUES (2, 2, 'b@x.y', 'connected', now())"))
        c.execute(text("INSERT INTO emails (gmail_account_id, id, attempts) VALUES (2, 'm1', 0)"))
    sf = make_session_factory(URL)
    raw = text("SELECT count(*) FROM emails")  # plain SQL: no ORM criteria
    with account_scope(1), sf() as s:
        assert s.execute(raw).scalar_one() == 2
    with account_scope(2), sf() as s:
        assert s.execute(raw).scalar_one() == 1
    with sf() as s:  # no scope at all: the policy hides every row
        assert s.execute(raw).scalar_one() == 0
    with account_scope(1), sf.begin() as s, pytest.raises(Exception, match="row-level security"):
        s.execute(text("INSERT INTO emails (gmail_account_id, id, attempts) VALUES (2, 'zz', 0)"))
    with account_scope(2), sf() as s:  # ORM path agrees
        assert [e.id for e in s.query(Email).all()] == ["m1"]
    sf.kw["bind"].dispose()


def test_downgrade_refuses_with_two_accounts(pg: tuple[Config, Engine]) -> None:
    cfg, engine = pg
    _seed(engine)
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        c.execute(text("INSERT INTO gmail_accounts (user_id, email_address, status, connected_at)"
                       " VALUES (2, 'b@x.y', 'connected', now())"))
    with pytest.raises(RuntimeError, match=r"downgrade de 0021 só com uma conta \(há 2\)"):
        command.downgrade(cfg, "0020")


def test_downgrade_restores_single_user(pg: tuple[Config, Engine]) -> None:
    cfg, engine = pg
    _seed(engine)
    before = _counts(engine, MOVED)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0020")
    assert _counts(engine, MOVED) == before
    with engine.connect() as c:
        assert tuple(c.execute(text("SELECT email_address, history_id FROM watch_state")).one()) == (
            "joao@example.com", "4242"
        )
