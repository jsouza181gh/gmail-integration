"""Settings, warnings, credentials and onboarding step 6 for the logged-in user."""
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.accounts import finish
from src.auth.onboarding import ONBOARDING_STEPS
from src.db.models import GmailAccount, Job, User, UserLlmKey
from src.db.scope import account_scope, system_scope
from src.platform.secrets import unseal
from tests.api_fakes import PASSWORD, SETTINGS, Api
from tests.panel_fakes import add_user, login

pytestmark = pytest.mark.noscope

GOOD_KEY = "AIza" + "x" * 35


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", SETTINGS.app_secret)


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    a = Api(session_factory)
    a.login()
    return a


def _set_gmail(api: Api, status: str) -> None:
    with system_scope(), api.sf.begin() as s:
        acc = s.get(GmailAccount, 1)
        assert acc is not None
        acc.status = status


def test_settings_shows_account_and_no_secrets(api: Api) -> None:
    body = api.client.get("/api/settings").json()
    assert body["username"] == "joao" and body["gmail_email"] == "me@example.com"
    assert body["has_password"] and body["has_totp"]
    assert body["llm_keys"] == {"configured": False, "has_backfill_key": False,
                                "validated_at": None, "last_error": None}


def test_replace_key_validates_then_stores_sealed(api: Api) -> None:
    r = api.client.put("/api/settings/llm-keys", json={"app_key": "curta"})
    assert r.status_code == 422 and "Gemini" in r.json()["detail"]
    r = api.client.put("/api/settings/llm-keys", json={"app_key": GOOD_KEY, "backfill_key": GOOD_KEY})
    assert r.status_code == 200 and r.json()["configured"] and r.json()["has_backfill_key"]
    assert GOOD_KEY not in r.text
    with api.sf() as s:
        row = s.get(UserLlmKey, 1)
        assert row is not None and row.app_key_enc != GOOD_KEY
        assert unseal(row.app_key_enc) == GOOD_KEY


def test_replace_key_clears_the_old_error_and_can_drop_backfill_key(api: Api) -> None:
    api.client.put("/api/settings/llm-keys", json={"app_key": GOOD_KEY, "backfill_key": GOOD_KEY})
    with api.sf.begin() as s:
        row = s.get(UserLlmKey, 1)
        assert row is not None
        row.last_error = "API key not valid"
    assert [w["kind"] for w in api.client.get("/api/warnings").json()] == ["key_invalid"]
    new = "AIza" + "y" * 35
    r = api.client.put("/api/settings/llm-keys", json={"app_key": new, "drop_backfill": True})
    assert r.json()["last_error"] is None and not r.json()["has_backfill_key"]
    assert api.client.get("/api/warnings").json() == []


def test_first_key_needs_the_main_key(api: Api) -> None:
    r = api.client.put("/api/settings/llm-keys", json={"backfill_key": GOOD_KEY})
    assert r.status_code == 422


def test_warnings_follow_gmail_and_key_state(api: Api) -> None:
    assert [w["kind"] for w in api.client.get("/api/warnings").json()] == ["key_missing"]
    _set_gmail(api, "revoked")
    assert {w["kind"] for w in api.client.get("/api/warnings").json()} == {
        "gmail_revoked", "key_missing"}
    _set_gmail(api, "disconnected")
    assert "gmail_disconnected" in {w["kind"] for w in api.client.get("/api/warnings").json()}


def test_user_without_gmail_gets_clear_answer_not_500(
    api: Api,
) -> None:
    secret = add_user(api.sf, 5, "novo", with_account=False)
    novo = login(api, "novo", secret)
    assert [w["kind"] for w in novo.get("/api/warnings").json()] == ["no_gmail"]
    r = novo.get("/api/emails")
    assert r.status_code == 409 and r.json()["code"] == "no_account"
    assert novo.get("/api/settings").json()["gmail_email"] is None


def test_change_password_checks_current_and_ends_other_sessions(api: Api) -> None:
    other = api.new_client()
    other.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
    other.post("/api/auth/2fa", json={"code": api.code()})
    assert len(api.client.get("/api/auth/sessions").json()) == 2
    c = api.client
    assert c.post("/api/auth/password", json={"current_password": "errada",
                                              "new_password": "uma-senha-nova-123"}).status_code == 403
    assert c.post("/api/auth/password", json={"current_password": PASSWORD,
                                              "new_password": "curta"}).status_code == 422
    r = c.post("/api/auth/password", json={"current_password": PASSWORD,
                                           "new_password": "uma-senha-nova-123"})
    assert r.status_code == 204
    assert len(c.get("/api/auth/sessions").json()) == 1  # this one stays
    assert c.post("/api/auth/login", json={"username": "joao",
                                           "password": "uma-senha-nova-123"}).status_code == 204


def test_rotate_totp_needs_password_and_live_code(api: Api) -> None:
    c = api.client
    assert c.post("/api/auth/totp", json={"password": "errada", "code": api.code()}).status_code == 403
    assert c.post("/api/auth/totp", json={"password": PASSWORD, "code": "000000"}).status_code == 403
    r = c.post("/api/auth/totp", json={"password": PASSWORD, "code": api.code()})
    assert r.status_code == 200
    body = r.json()
    assert len(body["recovery_codes"]) == 10 and body["otpauth_uri"].startswith("otpauth://")
    new_code = pyotp.TOTP(body["secret"]).at(api.clock())
    fresh = api.new_client()
    assert fresh.post("/api/auth/login", json={"username": "joao", "password": PASSWORD}).status_code == 204
    assert fresh.post("/api/auth/2fa", json={"code": new_code}).status_code == 204
    # an old recovery code no longer works
    old = api.new_client()
    old.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
    assert old.post("/api/auth/2fa", json={"code": api.recovery[0]}).status_code == 401


def test_sign_out_other_sessions(api: Api) -> None:
    for _ in range(2):
        other = api.new_client()
        other.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
        other.post("/api/auth/2fa", json={"code": api.code()})
    r = api.client.delete("/api/auth/sessions")
    assert r.json() == {"ended": 2}
    sessions = api.client.get("/api/auth/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"]


def test_sign_out_others_never_touches_another_user(api: Api) -> None:
    secret = add_user(api.sf, 2, "maria")
    maria = login(api, "maria", secret)
    api.client.delete("/api/auth/sessions")
    assert len(maria.get("/api/auth/sessions").json()) == 1


def test_step_six_is_appended_once() -> None:
    assert ONBOARDING_STEPS[-1] == finish.FINISH_STEP
    assert ONBOARDING_STEPS.count(finish.FINISH_STEP) == 1


def _step(api: Api, step: str | None) -> None:
    with system_scope(), api.sf.begin() as s:
        user = s.get(User, 1)
        assert user is not None
        user.onboarding_step = step


def _backfill_jobs(api: Api) -> int:
    with account_scope(1), api.sf() as s:
        return s.query(Job).filter(Job.kind == "backfill_page").count()


def test_finish_with_backfill_starts_it_and_ends_onboarding(api: Api) -> None:
    _step(api, "finish")
    r = api.client.post("/api/onboarding/finish", json={"backfill": True})
    assert r.status_code == 200 and r.json() == {"backfill_started": True}
    assert _backfill_jobs(api) == 1
    assert api.client.get("/api/auth/me").json()["onboarding_step"] is None


def test_finish_without_backfill_starts_nothing(api: Api) -> None:
    _step(api, "finish")
    assert api.client.post("/api/onboarding/finish", json={"backfill": False}).json() == {
        "backfill_started": False}
    assert _backfill_jobs(api) == 0


def test_finish_refused_before_the_earlier_steps(api: Api) -> None:
    _step(api, "totp")
    assert api.client.post("/api/onboarding/finish", json={"backfill": True}).status_code == 409
    assert _backfill_jobs(api) == 0
