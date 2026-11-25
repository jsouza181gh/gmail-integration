"""Security pages are per user; the admin's operational view is separate (spec: isolamento)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import SecurityEvent
from src.db.scope import system_scope
from tests.api_fakes import Api
from tests.panel_fakes import T0, add_user, login, make_admin

pytestmark = pytest.mark.noscope


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    return Api(session_factory)


def _world(api: Api) -> tuple[TestClient, TestClient]:
    """joao (user 1, admin) and maria (user 2, member), both logged in."""
    make_admin(api.sf, 1)
    api.login()
    secret = add_user(api.sf, 2, "maria")
    return api.client, login(api, "maria", secret)


def test_member_sees_only_own_events(api: Api) -> None:
    admin, maria = _world(api)
    mine = maria.get("/api/security/events").json()
    assert "login_ok" in {e["kind"] for e in mine}
    with api.sf() as s:
        owners = {e.user_id for e in s.query(SecurityEvent).filter(
            SecurityEvent.id.in_([e["id"] for e in mine]))}
    assert owners == {2}


def test_admin_events_are_own_too_not_the_members(api: Api) -> None:
    admin, maria = _world(api)
    with api.sf() as s:
        maria_ids = {e.id for e in s.query(SecurityEvent).filter(SecurityEvent.user_id == 2)}
    assert maria_ids
    seen = {e["id"] for e in admin.get("/api/security/events").json()}
    assert seen and not (seen & maria_ids)


def test_ip_blocks_only_for_admin(api: Api) -> None:
    admin, maria = _world(api)
    for _ in range(3):
        api.new_client().post("/api/auth/login", json={"username": "x", "password": "y"})
    assert maria.get("/api/security/blocks").json() == {"global_lock": False, "ips": []}
    assert admin.get("/api/security/blocks").json()["ips"]


def test_sessions_are_per_user(api: Api) -> None:
    admin, maria = _world(api)
    mine = maria.get("/api/auth/sessions").json()
    theirs = admin.get("/api/auth/sessions").json()
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0]["family_id"] != theirs[0]["family_id"]
    # cannot end somebody else's session, not even with its id
    assert maria.delete(f"/api/auth/sessions/{theirs[0]['family_id']}").status_code == 404
    assert len(admin.get("/api/auth/sessions").json()) == 1


def test_events_of_other_user_never_listed_even_with_kind_filter(api: Api) -> None:
    admin, maria = _world(api)
    with system_scope(), api.sf.begin() as s:
        s.add(SecurityEvent(kind="download", ip="9.9.9.9", detail="anexo secreto.pdf", user_id=1,
                            created_at=T0))
    got = maria.get("/api/security/events", params={"kind": "download"}).json()
    assert got == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/accounts"),
        ("GET", "/api/admin/invites"),
        ("POST", "/api/admin/invites"),
        ("DELETE", "/api/admin/invites/1"),
        ("POST", "/api/admin/users/1/reset-2fa"),
        ("GET", "/api/admin/security/events"),
    ],
)
def test_admin_routes_are_403_for_members(api: Api, method: str, path: str) -> None:
    _, maria = _world(api)
    body = {"email": "a@b.co"} if method == "POST" and path.endswith("invites") else None
    r = maria.request(method, path, json=body)
    assert r.status_code == 403


def test_admin_routes_are_401_without_login(api: Api) -> None:
    assert api.client.get("/api/admin/accounts").status_code == 401


def test_platform_events_for_admin_only_unattributed(api: Api) -> None:
    admin, _ = _world(api)
    for _ in range(3):
        api.new_client().post("/api/auth/login", json={"username": "x", "password": "y"})
    kinds = {e["kind"] for e in admin.get("/api/admin/security/events").json()}
    assert "ip_blocked" in kinds and "login_ok" not in kinds
