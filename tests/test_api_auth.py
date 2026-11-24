import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.api.security import client_ip
from src.db.models import SecurityEvent
from tests.api_fakes import ORIGIN, PASSWORD, SETTINGS, Api


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    return Api(session_factory)


def events(sf: sessionmaker[Session]) -> list[str]:
    with sf() as s:
        return list(s.scalars(select(SecurityEvent.kind).order_by(SecurityEvent.id)))


def cookie_header(resp_headers: list[tuple[str, str]], name: str) -> str:
    return next(v for k, v in resp_headers if k.lower() == "set-cookie" and v.startswith(f"{name}="))


def test_full_login_flow_sets_cookie_flags(api: Api) -> None:
    c = api.client
    r = c.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
    assert r.status_code == 204
    mfa = cookie_header(r.headers.multi_items(), "mfa")
    assert "HttpOnly" in mfa and "Secure" in mfa and "SameSite=strict" in mfa and "Path=/api/auth" in mfa
    assert c.get("/api/auth/me").status_code == 401  # password alone is not a session
    r = c.post("/api/auth/2fa", json={"code": api.code()})
    assert r.status_code == 204
    items = r.headers.multi_items()
    access, refresh, csrf = (cookie_header(items, n) for n in ("access", "refresh", "csrf"))
    assert all(f in access for f in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api", "Max-Age=600"))
    assert all(f in refresh for f in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/auth"))
    assert "HttpOnly" not in csrf and "Secure" in csrf and "Path=/" in csrf
    assert c.get("/api/auth/me").json() == {
        "username": "joao", "role": "member", "email": None, "onboarding_step": None,
    }


def test_wrong_password_401_then_blocked_423(api: Api) -> None:
    c = api.client
    codes = [c.post("/api/auth/login", json={"username": "joao", "password": "x"}).status_code
             for _ in range(3)]
    assert codes == [401, 401, 423]  # the third failure blocks at once
    r = c.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
    assert r.status_code == 423


def test_spoofed_xff_from_outside_ignored() -> None:
    trusted = SETTINGS.trusted_proxies
    assert client_ip("203.0.113.9", "1.2.3.4", trusted) == "203.0.113.9"
    assert client_ip("127.0.0.1", "6.6.6.6, 198.51.100.7", trusted) == "198.51.100.7"
    assert client_ip("127.0.0.1", None, trusted) == "127.0.0.1"
    assert client_ip("127.0.0.1", "garbage", trusted) == "127.0.0.1"


def test_xff_from_testclient_peer_not_trusted(api: Api) -> None:
    # TestClient's peer is "testclient": never a trusted proxy -> header ignored
    for i in range(3):
        api.client.post("/api/auth/login", json={"username": "joao", "password": "x"},
                        headers={"X-Forwarded-For": f"10.0.0.{i}"})
    with api.sf() as s:
        ips = set(s.scalars(select(SecurityEvent.ip)))
    assert ips == {"testclient"}


def test_write_without_csrf_or_wrong_origin_is_403(api: Api) -> None:
    api.login()
    c = api.client
    token = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/auth/logout").status_code == 403
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": "nope"}).status_code == 403
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": token, "Origin": "https://evil.test"}).status_code == 403
    assert events(api.sf).count("csrf_invalid") == 3
    assert c.get("/api/auth/me").status_code == 200  # session untouched
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204


def test_login_requires_matching_origin(api: Api) -> None:
    r = api.client.post("/api/auth/login", json={"username": "joao", "password": PASSWORD},
                        headers={"Origin": "https://evil.test"})
    assert r.status_code == 403


def test_protected_route_requires_access(api: Api) -> None:
    assert api.client.get("/api/auth/sessions").status_code == 401
    api.login()
    api.clock.advance(minutes=11)
    assert api.client.get("/api/auth/me").status_code == 401  # access expired


def test_refresh_endpoint_rotates_cookie(api: Api) -> None:
    api.login()
    old = api.client.cookies["refresh"]
    api.clock.advance(minutes=11)
    r = api.client.post("/api/auth/refresh")
    assert r.status_code == 204 and api.client.cookies["refresh"] != old
    api.client.headers["X-CSRF-Token"] = api.client.cookies["csrf"]
    assert api.client.get("/api/auth/me").status_code == 200


def test_sessions_list_and_revoke(api: Api) -> None:
    api.login()
    sessions = api.client.get("/api/auth/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"] is True
    family = sessions[0]["family_id"]
    assert api.client.delete(f"/api/auth/sessions/{family}").status_code == 204
    api.clock.advance(minutes=11)
    assert api.client.post("/api/auth/refresh").status_code == 401


def test_rate_limit_429(api: Api) -> None:
    codes = [api.client.post("/api/auth/login", json={"username": "x", "password": "y"},
                             headers={"X-Forwarded-For": "1.1.1.1"}).status_code for _ in range(12)]
    assert 429 in codes


def test_security_headers_present(api: Api) -> None:
    r = api.client.get("/healthz")
    h = r.headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert "max-age=31536000" in h["Strict-Transport-Security"]
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert api.client.get("/api/auth/me").headers["Cache-Control"] == "no-store"


def test_docs_disabled_in_production(api: Api) -> None:
    assert api.client.get("/docs").status_code == 404
    assert api.client.get("/openapi.json").status_code == 404


def test_recovery_code_login(api: Api) -> None:
    c = api.client
    c.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
    assert c.post("/api/auth/2fa", json={"code": api.recovery[0]}).status_code == 204


def test_origin_constant_matches_settings() -> None:
    assert SETTINGS.origin == ORIGIN
