"""Deleting an account on Postgres: real foreign keys and row-level security.
Runs when TEST_DATABASE_URL points at a migrated database (CI does this)."""
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

import src.accounts.exit as exit_mod
from src.accounts.exit import account_tables, delete_account, user_tables
from src.db.models import GmailAccount, User
from src.db.scope import system_scope
from src.db.session import make_session_factory
from src.gmail.clients import GmailClients
from tests.exit_seed import rows_of, seed_account

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida"),
    pytest.mark.noscope,
]

A, B = 9101, 9102  # account ids == user ids; far from real data


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    sf = make_session_factory(URL)
    monkeypatch.setattr(exit_mod, "stop_watch", lambda _c, _a: None)
    monkeypatch.setattr(exit_mod, "revoke_token", lambda _sf, _a: None)
    for n in (A, B):
        _clean(sf, n)
        with system_scope(), sf.begin() as s:
            now = datetime.now(UTC)
            s.add(User(id=n, username=f"exit-{n}", password_hash="h", totp_secret_enc="t",
                       created_at=now, password_changed_at=now))
            s.flush()
            s.add(GmailAccount(id=n, user_id=n, email_address=f"exit-{n}@example.com",
                               status="connected", connected_at=now))
        seed_account(sf, n, n)
    yield sf
    for n in (A, B):
        _clean(sf, n)


def _clean(sf: sessionmaker[Session], n: int) -> None:
    """Remove leftovers of a previous run with the production code path."""
    exit_mod.delete_account(sf, GmailClients(sf), n)


def test_delete_in_postgres_leaves_nothing_of_a(pg: sessionmaker[Session]) -> None:
    filled = rows_of(pg, A, A)
    for name in [*account_tables(), *user_tables()]:
        assert name in filled, f"{name} not seeded"
    before_b = rows_of(pg, B, B)
    delete_account(pg, GmailClients(pg), A)
    assert rows_of(pg, A, A) == {}
    assert rows_of(pg, B, B) == before_b
