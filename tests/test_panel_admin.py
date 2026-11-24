"""Admin: operational health without content, invites, 2FA reset."""
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.accounts.health import error_kind
from src.db.models import Email, SecurityEvent, GmailAccount, Job, User, UserLlmKey
from src.db.scope import account_scope, system_scope
from tests.api_fakes import SETTINGS, Api
from tests.panel_fakes import T0, add_user, login, make_admin

pytestmark = pytest.mark.noscope

SECRET_SUBJECT = "ASSUNTO-CONFIDENCIAL-DA-MARIA"


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", SETTINGS.app_secret)


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    a = Api(session_factory)
    make_admin(a.sf, 1)
    a.login()
    return a


def _job(sf: sessionmaker[Session], account: int, status: str, key: str) -> None:
    with account_scope(account), sf.begin() as s:
        s.add(Job(kind="process_email", priority=1, status=status, attempts=0, max_attempts=3,
                  run_after=T0, idempotency_key=key, email_id="e-" + key, created_at=T0))


def test_health_lists_states_and_counts_but_no_content(api: Api) -> None:
    add_user(api.sf, 2, "maria")
    with account_scope(2), api.sf.begin() as s:
        s.add(Email(id="m1", sender=f"{SECRET_SUBJECT} <x@y.z>", subject=SECRET_SUBJECT,
                    snippet=SECRET_SUBJECT, body=SECRET_SUBJECT, received_at=T0, attempts=0))
    with system_scope(), api.sf.begin() as s:
        acc = s.get(GmailAccount, 2)
        assert acc is not None
        acc.last_sync_at = datetime(2026, 11, 24, 8, 0, tzinfo=UTC)
        acc.last_error = f"HttpError 403 quota exceeded for {SECRET_SUBJECT}"
        s.add(UserLlmKey(user_id=2, app_key_enc="x", last_error="invalid"))
    _job(api.sf, 2, "queued", "a")
    _job(api.sf, 2, "running", "b")
    _job(api.sf, 2, "dead", "c")
    _job(api.sf, 1, "queued", "d")
    r = api.client.get("/api/admin/accounts")
    assert r.status_code == 200
    assert SECRET_SUBJECT not in r.text and "x@y.z" not in r.text
    by_email = {a["email"]: a for a in r.json()}
    maria = by_email["maria@example.com"]
    assert maria["queue_depth"] == 2 and maria["dead_jobs"] == 1
    assert maria["error_kind"] == "quota" and maria["key_ok"] is False
    assert maria["status"] == "connected" and maria["last_sync_at"].startswith("2026-11-24")
    assert by_email["me@example.com"]["queue_depth"] == 1
    assert by_email["me@example.com"]["key_ok"] is None


def test_error_kind_never_echoes_the_text() -> None:
    assert error_kind(None) is None
    assert error_kind("invalid_grant: Token has been revoked") == "revoked"
    assert error_kind("connection reset") == "network"
    assert error_kind("segredo estranho com assunto") == "other"


def test_reset_2fa_forces_new_enrolment_and_ends_sessions(api: Api) -> None:
    secret = add_user(api.sf, 2, "maria")
    maria = login(api, "maria", secret)
    assert api.client.post("/api/admin/users/2/reset-2fa").status_code == 204
    assert maria.get("/api/auth/me").status_code == 200  # access token lives a few minutes
    assert maria.post("/api/auth/refresh").status_code == 401  # but the session is gone
    with api.sf() as s:
        user = s.get(User, 2)
        assert user is not None
        assert not user.totp_secret_enc and user.onboarding_step == "totp"
    assert api.client.post("/api/admin/users/999/reset-2fa").status_code == 404


def test_reset_2fa_event_is_attributed_to_the_target(api: Api) -> None:
    add_user(api.sf, 2, "maria")
    api.client.post("/api/admin/users/2/reset-2fa")
    with api.sf() as s:
        ev = s.query(SecurityEvent).filter(SecurityEvent.kind == "totp_reset").one()
    assert ev.user_id == 2 and "#1" in ev.detail


def test_invite_create_list_revoke(api: Api) -> None:
    r = api.client.post("/api/admin/invites", json={"email": " Ana@Example.com "})
    assert r.status_code == 201 and r.json()["link"].startswith("/entrar?")
    assert api.client.post("/api/admin/invites", json={"email": "ana@example.com"}).status_code == 409
    assert api.client.post("/api/admin/invites", json={"email": "sem-arroba"}).status_code == 422
    items = api.client.get("/api/admin/invites").json()
    assert [(i["email"], i["state"]) for i in items] == [("ana@example.com", "pending")]
    assert api.client.delete(f"/api/admin/invites/{items[0]['id']}").status_code == 204
    assert api.client.get("/api/admin/invites").json()[0]["state"] == "revoked"
    assert api.client.delete("/api/admin/invites/999").status_code == 404
    # revoked invites can be re-issued
    assert api.client.post("/api/admin/invites", json={"email": "ana@example.com"}).status_code == 201
