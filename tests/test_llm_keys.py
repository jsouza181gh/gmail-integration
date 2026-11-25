from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import User, UserLlmKey
from src.db.scope import system_scope
from src.llm.keys import (
    AccountKeys,
    explain_key_error,
    key_status,
    keys_for_account,
    mark_key_error,
    save_keys,
    user_id_for_account,
)

FERNET = "2xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8="
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", FERNET)


def add_user(sf: sessionmaker[Session], user_id: int) -> None:
    with system_scope(), sf.begin() as s:
        s.add(User(id=user_id, username=f"u{user_id}", email=f"u{user_id}@x.org",
                   password_hash="x", totp_secret_enc="x", created_at=NOW,
                   password_changed_at=NOW))


def test_no_row_means_no_keys(session_factory: sessionmaker[Session]) -> None:
    assert keys_for_account(session_factory, 1) is None


def test_save_then_read_roundtrip_and_stored_sealed(
    session_factory: sessionmaker[Session],
) -> None:
    add_user(session_factory, 1)
    save_keys(session_factory, 1, app_key="AIza-app", backfill_key="AIza-bf", now=NOW)
    assert keys_for_account(session_factory, 1) == AccountKeys(app="AIza-app", backfill="AIza-bf")
    with system_scope(), session_factory() as s:
        row = s.scalars(select(UserLlmKey)).one()
    assert "AIza" not in row.app_key_enc and row.backfill_key_enc is not None
    assert "AIza" not in row.backfill_key_enc
    assert row.validated_at is not None and row.last_error is None


def test_save_replaces_and_clears_error(session_factory: sessionmaker[Session]) -> None:
    add_user(session_factory, 1)
    save_keys(session_factory, 1, app_key="old", backfill_key="bf", now=NOW)
    mark_key_error(session_factory, 1, "recusada")
    assert key_status(session_factory, 1).last_error == "recusada"
    save_keys(session_factory, 1, app_key="new", backfill_key=None, now=NOW)
    assert keys_for_account(session_factory, 1) == AccountKeys(app="new", backfill=None)
    assert key_status(session_factory, 1).last_error is None


def test_keys_are_per_account(session_factory: sessionmaker[Session], second_account: int) -> None:
    add_user(session_factory, 1)
    add_user(session_factory, 2)
    save_keys(session_factory, 1, app_key="a", backfill_key=None, now=NOW)
    assert keys_for_account(session_factory, second_account) is None


def test_unreadable_key_reads_as_missing(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    add_user(session_factory, 1)
    save_keys(session_factory, 1, app_key="a", backfill_key=None, now=NOW)
    monkeypatch.setenv("APP_SECRET", "3xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8=")
    assert keys_for_account(session_factory, 1) is None


def test_user_of_account(session_factory: sessionmaker[Session]) -> None:
    assert user_id_for_account(session_factory, 1) == 1
    assert user_id_for_account(session_factory, 99) is None


def test_status_without_row(session_factory: sessionmaker[Session]) -> None:
    status = key_status(session_factory, 1)
    assert not status.configured and not status.has_backfill_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key. API_KEY_INVALID",
         "não existe"),
        ("403 PERMISSION_DENIED. Generative Language API has not been used", "não tem acesso"),
        ("429 RESOURCE_EXHAUSTED. Quota exceeded ... PerDay", "cota do dia"),
        ("ConnectError: connection reset", "falar com o Google"),
    ],
)
def test_error_explained_for_a_layperson(raw: str, expected: str) -> None:
    assert expected in explain_key_error(raw)
