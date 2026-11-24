from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import system_scope
from src.gmail.accounts_store import (
    account_id_by_email,
    accounts_needing_renewal,
    mark_status,
    mark_synced,
    save_connection,
    syncable_account_ids,
)
from src.gmail.oauth import GMAIL_SCOPES, GoogleIdentity
from src.gmail.oauth_clients import platform_client, upsert_client
from src.platform.secrets import unseal
from tests.conftest import add_account
from tests.oauth_fakes import app_secret, connect  # noqa: F401

DAY_MS = 86_400_000


def account(sf: sessionmaker[Session], account_id: int = 1) -> GmailAccount:
    with system_scope(), sf() as s:
        acc = s.get_one(GmailAccount, account_id)
        s.expunge(acc)
        return acc


def test_save_connection_seals_the_token_and_connects(session_factory: sessionmaker[Session]) -> None:
    mark_status(session_factory, 1, "revoked", "invalid_grant")
    client = upsert_client(session_factory, "platform", "cid", "secret")
    identity = GoogleIdentity(email="me@example.com", email_verified=True, sub="s",
                              refresh_token="rt-x", scopes=GMAIL_SCOPES)
    save_connection(session_factory, 1, identity, client)
    acc = account(session_factory)
    assert acc.status == "connected" and acc.last_error is None
    assert acc.oauth_client_id == client
    assert acc.refresh_token_enc and "rt-x" not in acc.refresh_token_enc
    assert unseal(acc.refresh_token_enc) == "rt-x"
    assert acc.scopes == " ".join(GMAIL_SCOPES)


def test_account_id_by_email_only_for_connected(session_factory: sessionmaker[Session]) -> None:
    assert account_id_by_email(session_factory, " ME@example.com ") == 1
    assert account_id_by_email(session_factory, "nobody@example.com") is None
    mark_status(session_factory, 1, "revoked", None)
    assert account_id_by_email(session_factory, "me@example.com") is None


def test_mark_synced_sets_time_and_clears_error(session_factory: sessionmaker[Session]) -> None:
    mark_status(session_factory, 1, "connected", "boom")
    at = datetime(2026, 11, 24, 10, tzinfo=UTC)
    mark_synced(session_factory, 1, at)
    acc = account(session_factory)
    assert acc.last_sync_at is not None and acc.last_sync_at.replace(tzinfo=UTC) == at
    assert acc.last_error is None


def test_renewal_picks_connected_accounts_expiring_soon(
    session_factory: sessionmaker[Session],
) -> None:
    now = 1_000 * DAY_MS
    for i, (email, exp) in enumerate(
        [("a@x", now + DAY_MS), ("b@x", now + 5 * DAY_MS), ("c@x", None), ("d@x", now + DAY_MS)], start=2
    ):
        add_account(session_factory, i, email)
        connect(session_factory, i, email)
        with system_scope(), session_factory.begin() as s:
            s.get_one(GmailAccount, i).watch_expiration = exp
    mark_status(session_factory, 5, "revoked", None)  # revoked: left alone
    # account 1 has no token: left alone
    assert accounts_needing_renewal(session_factory, now, 2 * DAY_MS) == [2, 4]
    assert syncable_account_ids(session_factory) == [2, 3, 4]


def test_platform_client_round_trip_and_upsert(session_factory: sessionmaker[Session]) -> None:
    assert platform_client(session_factory) is None
    first = upsert_client(session_factory, "platform", "cid", "s1")
    assert upsert_client(session_factory, "platform", "cid", "s2") == first
    upsert_client(session_factory, "desktop", "cid-d", "sd")
    found = platform_client(session_factory)
    assert found is not None and (found.id, found.client_id, found.client_secret) == (first, "cid", "s2")
