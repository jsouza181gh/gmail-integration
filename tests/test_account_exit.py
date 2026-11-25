"""Leaving: disconnect keeps data, delete erases every row of the account."""
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import src.accounts.exit as exit_mod
from src.accounts.exit import (
    BACKUP_COPIES_KEPT,
    AccountProtected,
    account_tables,
    delete_account,
    disconnect,
    user_tables,
)
from src.db.models import Base, GmailAccount, Invite, User
from src.db.scope import system_scope
from src.gmail.clients import GmailClients
from tests.exit_seed import rows_of, seed_account

pytestmark = pytest.mark.noscope


def _user(sf: sessionmaker[Session], user_id: int, role: str = "member") -> None:
    with system_scope(), sf.begin() as s:
        s.add(User(id=user_id, username=f"u{user_id}", role=role, password_hash="h",
                   totp_secret_enc="t", created_at=datetime.now(UTC),
                   password_changed_at=datetime.now(UTC)))


class Google:
    """Records what the exit asked Google; `fail` makes both calls raise."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def stop(_clients: GmailClients, account_id: int) -> None:
            self.calls.append(f"stop {account_id}")
            if self.fail:
                raise RuntimeError("google fora do ar")

        def revoke(_sf: sessionmaker[Session], account_id: int) -> None:
            self.calls.append(f"revoke {account_id}")
            if self.fail:
                raise RuntimeError("google fora do ar")

        monkeypatch.setattr(exit_mod, "stop_watch", stop)
        monkeypatch.setattr(exit_mod, "revoke_token", revoke)


@pytest.fixture
def two(session_factory: sessionmaker[Session], second_account: int) -> sessionmaker[Session]:
    """Accounts 1 (user 1) and 2 (user 2), both fully populated."""
    for n in (1, 2):
        _user(session_factory, n)
        seed_account(session_factory, n, n)
    return session_factory


def test_account_tables_cover_every_table_with_gmail_account_id() -> None:
    expected = {t.name for t in Base.metadata.tables.values() if "gmail_account_id" in t.c}
    assert set(account_tables()) == expected
    assert {"emails", "jobs", "account_settings", "gmail_labels", "chat_cards"} <= expected


def test_user_tables_cover_every_table_pointing_at_users() -> None:
    names = set(user_tables())
    assert {"auth_sessions", "recovery_codes"} <= names


def test_seed_really_filled_every_table(two: sessionmaker[Session]) -> None:
    filled = rows_of(two, 1, 1)
    for name in [*account_tables(), *user_tables()]:
        assert name in filled, f"{name} not seeded"


def test_delete_leaves_nothing_of_a_and_b_untouched(
    two: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    google = Google()
    google.install(monkeypatch)
    before_b = rows_of(two, 2, 2)
    delete_account(two, GmailClients(two), 1)
    assert rows_of(two, 1, 1) == {}
    assert rows_of(two, 2, 2) == before_b
    assert google.calls == ["stop 1", "revoke 1"]


def test_google_failure_does_not_block_delete(
    two: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    google = Google(fail=True)
    google.install(monkeypatch)
    delete_account(two, GmailClients(two), 1)
    assert rows_of(two, 1, 1) == {}
    assert google.calls == ["stop 1", "revoke 1"]  # revoke still tried after stop failed


def test_admin_cannot_be_deleted(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _user(session_factory, 1, role="admin")
    seed_account(session_factory, 1, 1)
    google = Google()
    google.install(monkeypatch)
    with pytest.raises(AccountProtected):
        delete_account(session_factory, GmailClients(session_factory), 1)
    assert google.calls == []  # refused before touching Google
    assert rows_of(session_factory, 1, 1)


def test_disconnect_keeps_data_and_history(
    two: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with system_scope(), two.begin() as s:
        s.get(GmailAccount, 1).history_id = "4242"  # type: ignore[union-attr]
    before = rows_of(two, 1, 1)
    google = Google()
    google.install(monkeypatch)
    disconnect(two, GmailClients(two), 1)
    with system_scope(), two() as s:
        account = s.get(GmailAccount, 1)
        assert account is not None
        assert (account.status, account.history_id) == ("disconnected", "4242")
        assert s.scalar(select(GmailAccount.status).where(GmailAccount.id == 2)) == "connected"
    assert rows_of(two, 1, 1) == {**before, "gmail_accounts": 1}
    assert google.calls == ["stop 1", "revoke 1"]


def test_disconnect_tolerates_google_failure(
    two: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    Google(fail=True).install(monkeypatch)
    disconnect(two, GmailClients(two), 1)
    with system_scope(), two() as s:
        assert s.scalar(select(GmailAccount.status).where(GmailAccount.id == 1)) == "disconnected"


def test_backup_retention_matches_deploy_script() -> None:
    script = (Path(__file__).parent.parent / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    match = re.search(r"^BACKUPS_KEPT=(\d+)", script, re.MULTILINE)
    assert match is not None and int(match.group(1)) == BACKUP_COPIES_KEPT



def test_delete_keeps_the_invite_so_coming_back_needs_a_new_one(
    two: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    Google().install(monkeypatch)
    now = datetime.now(UTC)
    with system_scope(), two.begin() as s:
        s.add(User(id=9, username="admin", role="admin", password_hash="h", totp_secret_enc="t",
                   created_at=now, password_changed_at=now))
        s.flush()
        s.add(Invite(email="u1@example.com", token_hash="x" * 64, created_by=9, created_at=now,
                     accepted_at=now))
    assert "invites" not in user_tables()
    delete_account(two, GmailClients(two), 1)
    with system_scope(), two() as s:
        invite = s.scalars(select(Invite)).one()
        assert invite.email == "u1@example.com" and invite.accepted_at is not None
