from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.auth.invites import InviteError, invite_for_token
from src.cli.invite import invite
from src.db.models import User

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def _users(sf: sessionmaker[Session]) -> None:
    with sf.begin() as s:
        s.add(User(id=1, username="joao", email="joao@example.com", role="admin", created_at=NOW,
                   password_changed_at=NOW))
        s.add(User(id=2, username="bia", email="bia@example.com", role="member", created_at=NOW,
                   password_changed_at=NOW))


def test_invite_by_the_first_admin_prints_a_usable_link(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ORIGIN", "https://panel.test")
    _users(session_factory)
    link = invite(session_factory, "Ana@Example.com", None)
    assert link.startswith("https://panel.test/entrar?convite=")
    found = invite_for_token(session_factory, link.split("convite=")[1])
    assert found is not None and found.email == "ana@example.com" and found.created_by == 1


def test_invite_by_a_named_admin_and_errors(session_factory: sessionmaker[Session]) -> None:
    _users(session_factory)
    assert invite(session_factory, "ana@example.com", "joao@example.com")
    with pytest.raises(InviteError, match="administrador"):
        invite(session_factory, "ana@example.com", "bia@example.com")  # a member cannot invite
    with pytest.raises(InviteError, match="e-mail"):
        invite(session_factory, "lixo", None)


def test_invite_needs_an_admin(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(InviteError, match="administrador"):
        invite(session_factory, "ana@example.com", None)
