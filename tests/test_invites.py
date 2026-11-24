from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.auth.invites import (
    InviteError,
    accept,
    create_invite,
    invite_for_token,
    list_invites,
    open_invite_for,
    revoke,
)
from src.db.models import User

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ORIGIN", "https://panel.test/")


def _admin(sf: sessionmaker[Session]) -> int:
    with sf.begin() as s:
        s.add(User(id=1, username="joao", created_at=NOW, password_changed_at=NOW, role="admin"))
    return 1


def _token(link: str) -> str:
    assert link.startswith("https://panel.test/entrar?convite=")
    return link.split("convite=")[1]


def test_create_returns_link_and_stores_only_the_hash(session_factory: sessionmaker[Session]) -> None:
    admin = _admin(session_factory)
    link = create_invite(session_factory, "  Ana@Example.COM ", admin, now=NOW)
    raw = _token(link)
    row = invite_for_token(session_factory, raw)
    assert row is not None and row.email == "ana@example.com" and row.created_by == admin
    from src.db.models import Invite

    with session_factory() as s:
        stored = s.query(Invite).one()
    assert raw not in (stored.token_hash, stored.email) and len(stored.token_hash) == 64


def test_unknown_or_malformed_token_finds_nothing(session_factory: sessionmaker[Session]) -> None:
    _admin(session_factory)
    assert invite_for_token(session_factory, "nope") is None
    assert invite_for_token(session_factory, "") is None


def test_one_invite_per_email_reissue_changes_the_token(session_factory: sessionmaker[Session]) -> None:
    admin = _admin(session_factory)
    first = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    second = _token(create_invite(session_factory, "ANA@example.com", admin, now=NOW))
    assert first != second
    assert invite_for_token(session_factory, first) is None  # the old link died
    assert invite_for_token(session_factory, second) is not None
    assert len(list_invites(session_factory)) == 1


def test_revoked_invite_does_not_open_and_can_be_reissued(session_factory: sessionmaker[Session]) -> None:
    admin = _admin(session_factory)
    raw = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    row = invite_for_token(session_factory, raw)
    assert row is not None
    assert revoke(session_factory, row.id, now=NOW) is True
    assert revoke(session_factory, row.id, now=NOW) is False
    assert invite_for_token(session_factory, raw) is None
    assert open_invite_for(session_factory, "ana@example.com", raw) is None
    again = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    assert invite_for_token(session_factory, again) is not None
    assert [i.status for i in list_invites(session_factory)] == ["open"]


def test_accepted_invite_is_closed_and_blocks_reissue_while_the_user_exists(
    session_factory: sessionmaker[Session],
) -> None:
    admin = _admin(session_factory)
    raw = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    row = invite_for_token(session_factory, raw)
    assert row is not None
    accept(session_factory, row.id, now=NOW)
    assert invite_for_token(session_factory, raw) is None
    assert [i.status for i in list_invites(session_factory)] == ["accepted"]
    with session_factory.begin() as s:
        s.add(User(id=2, username="ana", email="ana@example.com", created_at=NOW,
                   password_changed_at=NOW))
    with pytest.raises(InviteError, match="já tem conta"):
        create_invite(session_factory, "ana@example.com", admin, now=NOW)


def test_after_the_account_is_deleted_a_new_invite_reopens_it(
    session_factory: sessionmaker[Session],
) -> None:
    admin = _admin(session_factory)
    raw = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    row = invite_for_token(session_factory, raw)
    assert row is not None
    accept(session_factory, row.id, now=NOW)  # and the user never existed / was deleted
    fresh = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    assert invite_for_token(session_factory, fresh) is not None


def test_open_invite_for_requires_the_token_and_the_same_email(
    session_factory: sessionmaker[Session],
) -> None:
    admin = _admin(session_factory)
    raw = _token(create_invite(session_factory, "ana@example.com", admin, now=NOW))
    assert open_invite_for(session_factory, "ana@example.com", raw) is not None
    assert open_invite_for(session_factory, "Ana@Example.com", raw) is not None
    assert open_invite_for(session_factory, "bob@example.com", raw) is None
    assert open_invite_for(session_factory, "ana@example.com", None) is None
    assert open_invite_for(session_factory, "ana@example.com", "wrong") is None


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "a b@c.com", "x@y.com\nz"])
def test_rejects_bad_emails(session_factory: sessionmaker[Session], bad: str) -> None:
    admin = _admin(session_factory)
    with pytest.raises(InviteError, match="e-mail"):
        create_invite(session_factory, bad, admin, now=NOW)
