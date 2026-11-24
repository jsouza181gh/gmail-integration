import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import account_scope, system_scope
from src.gmail.clients import GmailClients, GmailNotConnected, GmailRevoked
from src.gmail.oauth import GMAIL_MODIFY, GMAIL_SETTINGS
from src.platform.secrets import unseal
from tests.oauth_fakes import FakeRefresher, app_secret, connect  # noqa: F401


class Built:
    def __init__(self) -> None:
        self.tokens: list[str | None] = []

    def __call__(self, creds: object) -> object:
        self.tokens.append(getattr(creds, "token", None))
        return object()


def make(sf: sessionmaker[Session]) -> tuple[GmailClients, FakeRefresher, Built]:
    refresher, built = FakeRefresher(), Built()
    clients = GmailClients(sf, refresh=refresher, build_service=built)
    return clients, refresher, built


def row(sf: sessionmaker[Session], account_id: int = 1) -> GmailAccount:
    with system_scope(), sf() as s:
        acc = s.get_one(GmailAccount, account_id)
        s.expunge(acc)
        return acc


def test_service_uses_the_decrypted_refresh_token(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com", refresh_token="rt-secret")
    clients, refresher, built = make(session_factory)
    clients.service_for(1)
    assert refresher.used == ["rt-secret"]
    assert built.tokens == ["access-1"]


def test_access_token_is_cached_between_calls(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com")
    clients, refresher, built = make(session_factory)
    clients.service_for(1)
    clients.service_for(1)
    assert len(refresher.used) == 1
    assert built.tokens == ["access-1", "access-1"]


def test_reconnect_drops_the_cached_token(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com", refresh_token="rt-1")
    clients, refresher, _ = make(session_factory)
    clients.service_for(1)
    connect(session_factory, 1, "me@example.com", refresh_token="rt-2")
    clients.service_for(1)
    assert refresher.used == ["rt-1", "rt-2"]


def test_current_follows_the_account_scope(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    connect(session_factory, 1, "me@example.com", refresh_token="rt-a")
    connect(session_factory, 2, "other@example.com", refresh_token="rt-b")
    clients, refresher, _ = make(session_factory)
    getter = clients.getter()
    with account_scope(2):
        getter()
    getter()  # the autouse fixture runs as account 1
    assert refresher.used == ["rt-b", "rt-a"]


def test_rotated_refresh_token_is_saved_back_sealed(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com", refresh_token="rt-1")
    clients, refresher, _ = make(session_factory)
    refresher.rotate_to = "rt-new"
    clients.service_for(1)
    stored = row(session_factory).refresh_token_enc
    assert stored is not None and stored != "rt-new" and unseal(stored) == "rt-new"


def test_invalid_grant_revokes_the_account_and_alerts(
    session_factory: sessionmaker[Session],
) -> None:
    connect(session_factory, 1, "me@example.com")
    clients, refresher, _ = make(session_factory)
    seen: list[tuple[int, str]] = []
    clients.on_revoked = lambda account, email: seen.append((account, email))
    refresher.fail = "invalid_grant: Token has been expired or revoked."
    with pytest.raises(GmailRevoked):
        clients.service_for(1)
    acc = row(session_factory)
    assert acc.status == "revoked" and acc.last_error and "invalid_grant" in acc.last_error
    assert seen == [(1, "me@example.com")]
    with pytest.raises(GmailRevoked):  # already revoked: no new call, no new alert
        clients.service_for(1)
    assert len(refresher.used) == 1 and len(seen) == 1


def test_other_refresh_errors_do_not_revoke(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com")
    clients, refresher, _ = make(session_factory)
    refresher.fail = "temporarily_unavailable"
    with pytest.raises(RefreshError):
        clients.service_for(1)
    assert row(session_factory).status == "connected"


def test_account_without_token_is_not_connected(session_factory: sessionmaker[Session]) -> None:
    clients, _, _ = make(session_factory)
    with pytest.raises(GmailNotConnected):
        clients.service_for(1)
    with pytest.raises(GmailNotConnected):
        clients.service_for(999)


def test_missing_modify_scope_is_refused(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com", scopes=["openid", "email"])
    clients, _, _ = make(session_factory)
    with pytest.raises(GmailNotConnected):
        clients.service_for(1)


def test_has_scope_reads_the_granted_scopes(session_factory: sessionmaker[Session]) -> None:
    clients, _, _ = make(session_factory)
    assert not clients.has_scope(GMAIL_SETTINGS)
    connect(session_factory, 1, "me@example.com")
    assert clients.has_scope(GMAIL_SETTINGS)
    connect(session_factory, 1, "me@example.com", scopes=[GMAIL_MODIFY])
    assert not clients.has_scope(GMAIL_SETTINGS)
