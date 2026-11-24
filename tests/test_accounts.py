"""Per-account watch state and runtime settings; platform account lookups."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import RuntimeSettings
from src.db.accounts import (
    account_by_email,
    account_for_user,
    admin_account_id,
    connected_account_ids,
)
from src.db.models import GmailAccount, User
from src.db.scope import account_scope, system_scope
from src.db.store import Store


def test_watch_state_per_account(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    store = Store(session_factory)
    store.init_watch_state("me@example.com", "100", 1)
    store.init_watch_state("me@example.com", "999", 2)  # renewal keeps the cursor
    with account_scope(second_account):
        assert store.get_watch_state() is None
        store.init_watch_state("other@example.com", "7", 3)
        state = store.get_watch_state()
        assert state is not None and state.history_id == "7"
    state = store.get_watch_state()
    assert state is not None and (state.history_id, state.expiration) == ("100", 2)


def test_settings_per_account(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    settings = RuntimeSettings(session_factory)
    settings.set_backfill_paused(True)
    settings.set_backfill_turbo(True)
    settings.set_llm_mode("fast")
    assert settings.backfill_paused() and settings.backfill_turbo()
    assert settings.llm_mode() == "fast"
    with account_scope(second_account):
        assert not settings.backfill_paused() and not settings.backfill_turbo()
        assert settings.llm_mode() is None


def _user(sf: sessionmaker[Session], user_id: int, role: str) -> None:
    now = datetime.now(UTC)
    with system_scope(), sf.begin() as s:
        s.add(User(id=user_id, username=f"u{user_id}", password_hash="x", totp_secret_enc="x",
                   created_at=now, password_changed_at=now, role=role))


def test_admin_account_id(session_factory: sessionmaker[Session], second_account: int) -> None:
    _user(session_factory, 1, "member")
    _user(session_factory, 2, "admin")
    assert admin_account_id(session_factory) == second_account


def test_account_for_user(session_factory: sessionmaker[Session], second_account: int) -> None:
    assert account_for_user(session_factory, 2) == second_account
    assert account_for_user(session_factory, 99) is None


def test_account_by_email_is_case_insensitive(session_factory: sessionmaker[Session]) -> None:
    assert account_by_email(session_factory, "  ME@Example.com ") == 1
    assert account_by_email(session_factory, "nobody@example.com") is None


def test_connected_ids_skip_disconnected(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    with system_scope(), session_factory.begin() as s:
        s.get_one(GmailAccount, second_account).status = "disconnected"
    assert connected_account_ids(session_factory) == [1]
