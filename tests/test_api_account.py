"""/api/account: disconnect, delete with re-confirmation, admin protection."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import src.accounts.exit as exit_mod
from src.db.models import AuthSession, GmailAccount, User
from src.db.scope import system_scope
from src.gmail.clients import GmailClients
from tests.api_fakes import PASSWORD, Api
from tests.exit_seed import rows_of, seed_account

pytestmark = pytest.mark.noscope

EMAIL = "me@example.com"


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(exit_mod, "stop_watch", lambda _c, a: calls.append(f"stop {a}"))
    monkeypatch.setattr(exit_mod, "revoke_token", lambda _sf, a: calls.append(f"revoke {a}"))
    return calls


def _api(sf: sessionmaker[Session]) -> Api:
    api = Api(sf, clients=GmailClients(sf))
    api.login()
    return api


def _set_role(sf: sessionmaker[Session], role: str) -> None:
    with system_scope(), sf.begin() as s:
        user = s.scalars(select(User).where(User.username == "joao")).one()
        user.role = role


def test_info(session_factory: sessionmaker[Session]) -> None:
    api = _api(session_factory)
    body = api.client.get("/api/account").json()
    assert body == {"email": EMAIL, "status": "connected", "can_delete": True, "backup_copies": 7}


def test_requires_login(session_factory: sessionmaker[Session]) -> None:
    api = Api(session_factory, clients=GmailClients(session_factory))
    assert api.client.get("/api/account").status_code == 401


def test_disconnect(session_factory: sessionmaker[Session], google: list[str]) -> None:
    api = _api(session_factory)
    assert api.client.post("/api/account/disconnect").status_code == 204
    with system_scope(), session_factory() as s:
        assert s.get(GmailAccount, 1).status == "disconnected"  # type: ignore[union-attr]
    assert google == ["stop 1", "revoke 1"]
    assert api.client.get("/api/account").json()["status"] == "disconnected"


def test_delete_with_password(
    session_factory: sessionmaker[Session], second_account: int, google: list[str]
) -> None:
    api = _api(session_factory)
    seed_account(session_factory, 1, 1)
    r = api.client.post("/api/account/delete", json={"email": "ME@Example.com", "password": PASSWORD})
    assert r.status_code == 204, r.text
    assert rows_of(session_factory, 1, 1) == {}
    assert google == ["stop 1", "revoke 1"]
    assert api.client.get("/api/account").status_code == 401  # session gone


def test_delete_with_totp_code(session_factory: sessionmaker[Session], google: list[str]) -> None:
    api = _api(session_factory)
    r = api.client.post("/api/account/delete", json={"email": EMAIL, "code": api.code()})
    assert r.status_code == 204, r.text
    assert rows_of(session_factory, 1, 1) == {}


def test_delete_needs_the_typed_email(session_factory: sessionmaker[Session], google: list[str]) -> None:
    api = _api(session_factory)
    r = api.client.post("/api/account/delete", json={"email": "other@example.com", "password": PASSWORD})
    assert r.status_code == 422
    assert google == []
    assert rows_of(session_factory, 1, 1)


def test_delete_needs_reconfirmation(session_factory: sessionmaker[Session], google: list[str]) -> None:
    api = _api(session_factory)
    wrong = api.client.post("/api/account/delete", json={"email": EMAIL, "password": "errada-errada"})
    none = api.client.post("/api/account/delete", json={"email": EMAIL})
    assert (wrong.status_code, none.status_code) == (401, 401)
    assert google == []
    with system_scope(), session_factory() as s:
        assert s.get(User, 1) is not None
        assert s.scalars(select(AuthSession)).first() is not None


def test_admin_cannot_delete_itself(session_factory: sessionmaker[Session], google: list[str]) -> None:
    api = _api(session_factory)
    _set_role(session_factory, "admin")
    assert api.client.get("/api/account").json()["can_delete"] is False
    r = api.client.post("/api/account/delete", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403
    assert "administrador" in r.json()["detail"]
    assert google == []
    assert rows_of(session_factory, 1, 1)


def test_delete_keeps_the_other_account(
    session_factory: sessionmaker[Session], second_account: int, google: list[str]
) -> None:
    api = _api(session_factory)
    with system_scope(), session_factory.begin() as s:
        s.add(User(id=2, username="u2", password_hash="h", totp_secret_enc="t",
                   created_at=api.clock(), password_changed_at=api.clock()))
    seed_account(session_factory, 2, 2)
    before = rows_of(session_factory, 2, 2)
    assert api.client.post(
        "/api/account/delete", json={"email": EMAIL, "password": PASSWORD}
    ).status_code == 204
    assert rows_of(session_factory, 2, 2) == before
