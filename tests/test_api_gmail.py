"""Gmail per account over HTTP: status, reconnect (OAuth) and the push webhook."""
import base64
import json
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.db.models import GmailAccount
from src.db.scope import system_scope
from src.gmail import oauth
from src.gmail.accounts_store import account_id_by_email, mark_status
from src.gmail.oauth import GMAIL_SCOPES, NONCE_COOKIE, authorization_url
from src.gmail.oauth_clients import upsert_client
from src.platform.secrets import unseal
from src.services.sync import SyncCoordinators
from tests.api_fakes import ORIGIN, Api
from tests.conftest import add_account
from tests.oauth_fakes import FakeGoogle, app_secret, connect  # noqa: F401

pytestmark = pytest.mark.noscope


def account(sf: sessionmaker[Session], account_id: int = 1) -> GmailAccount:
    with system_scope(), sf() as s:
        acc = s.get_one(GmailAccount, account_id)
        s.expunge(acc)
        return acc


class Panel:
    """An `Api` whose OAuth talks to a fake Google."""

    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.connected: list[int] = []
        self.api = Api(sf, on_connected=self.connected.append)
        self.google = FakeGoogle()
        upsert_client(sf, "platform", "cid.apps.googleusercontent.com", "csecret")
        oauth.configure(sf, ORIGIN, self.google)
        self.api.login()
        self.client = self.api.client

    def state(self, nonce: str = "browser-nonce") -> str:
        """A state bound to this browser: the nonce is also in its cookie."""
        url = authorization_url("connect", user_id=1, invite_token=None, nonce=nonce)
        self.client.cookies.set(NONCE_COOKIE, "browser-nonce")
        return parse_qs(urlparse(url).query)["state"][0]

    def callback(self, **params: str) -> tuple[int, str]:
        r = self.client.get("/api/oauth/google/callback", params=params, follow_redirects=False)
        return r.status_code, r.headers.get("location", "")


@pytest.fixture
def panel(session_factory: sessionmaker[Session]) -> Iterator[Panel]:
    yield Panel(session_factory)
    oauth._runtime = None


# --- status ------------------------------------------------------------------

def test_status_of_a_connected_account(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    connect(session_factory, 1, "me@example.com")
    body = panel.client.get("/api/gmail/status").json()
    assert body == {
        "email_address": "me@example.com", "status": "connected",
        "last_sync_at": None, "last_error": None,
    }


def test_status_without_a_token_asks_to_reconnect(panel: Panel) -> None:
    body = panel.client.get("/api/gmail/status").json()
    assert body["status"] == "disconnected" and "Reconectar" in body["last_error"]


def test_status_revoked_shows_the_error(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    connect(session_factory, 1, "me@example.com")
    mark_status(session_factory, 1, "revoked", "Google revogou o acesso (invalid_grant)")
    body = panel.client.get("/api/gmail/status").json()
    assert (body["status"], body["last_error"]) == ("revoked", "Google revogou o acesso (invalid_grant)")


def test_status_needs_login(panel: Panel) -> None:
    anonymous = TestClient(panel.client.app, base_url="https://testserver")
    assert anonymous.get("/api/gmail/status").status_code == 401


# --- reconnect ---------------------------------------------------------------

def test_connect_returns_the_google_url(panel: Panel) -> None:
    r = panel.client.post("/api/oauth/google/connect")
    assert r.status_code == 200
    url = urlparse(r.json()["url"])
    assert (url.netloc, url.path) == ("accounts.google.com", "/o/oauth2/v2/auth")
    assert parse_qs(url.query)["scope"][0].split() == GMAIL_SCOPES


def test_connect_needs_login(panel: Panel) -> None:
    anonymous = TestClient(panel.client.app, base_url="https://testserver",
                           headers={"Origin": ORIGIN})
    assert anonymous.post("/api/oauth/google/connect").status_code in (401, 403)  # CSRF or session


def test_connect_without_configured_client_is_503(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    with system_scope(), session_factory.begin() as s:
        s.execute(text("delete from oauth_clients"))
    assert panel.client.post("/api/oauth/google/connect").status_code == 503


def test_callback_reconnects_with_the_same_email(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    mark_status(session_factory, 1, "revoked", "invalid_grant")
    status, location = panel.callback(code="c", state=panel.state())
    assert (status, location) == (303, f"{ORIGIN}/seguranca?gmail=ok")
    acc = account(session_factory)
    assert acc.status == "connected" and acc.last_error is None
    assert acc.refresh_token_enc and unseal(acc.refresh_token_enc) == "rt-from-google"
    assert panel.connected == [1]


def test_callback_refuses_another_google_account(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    panel.google.email = "someone.else@example.com"
    status, location = panel.callback(code="c", state=panel.state())
    assert (status, location) == (303, f"{ORIGIN}/seguranca?gmail=outro-email")
    assert account(session_factory).refresh_token_enc is None and panel.connected == []


def test_callback_without_refresh_token_or_gmail_permission(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    panel.google.refresh_token = None
    assert panel.callback(code="c", state=panel.state())[1].endswith("gmail=sem-token")
    panel.google.refresh_token, panel.google.scope = "rt", "openid email"
    assert panel.callback(code="c", state=panel.state())[1].endswith("gmail=sem-permissao")
    assert account(session_factory).refresh_token_enc is None


def test_callback_refuses_a_state_started_in_another_browser(
    session_factory: sessionmaker[Session], panel: Panel
) -> None:
    """Login CSRF: a state minted for someone else's browser is refused."""
    mark_status(session_factory, 1, "revoked", "invalid_grant")
    assert panel.callback(code="c", state=panel.state(nonce="attacker"))[1].endswith("gmail=erro")
    state = panel.state()
    panel.client.cookies.clear()
    assert panel.callback(code="c", state=state)[1].endswith("gmail=erro")
    assert account(session_factory).refresh_token_enc is None and panel.google.calls == []


def test_connect_sets_the_nonce_cookie_of_its_state(panel: Panel) -> None:
    r = panel.client.post("/api/oauth/google/connect")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{NONCE_COOKIE}=") and "HttpOnly" in cookie
    assert "Path=/api/oauth/google" in cookie and "SameSite=lax" in cookie
    nonce = cookie.split(";")[0].split("=", 1)[1]
    state = oauth.read_state(parse_qs(urlparse(r.json()["url"]).query)["state"][0])
    assert state.nonce == nonce and len(nonce) >= 32


def test_callback_when_the_user_says_no_or_the_state_is_bad(panel: Panel) -> None:
    assert panel.callback(error="access_denied", state=panel.state())[1].endswith("gmail=cancelado")
    assert panel.callback(code="c", state="forged")[1].endswith("/?oauth=erro")
    assert panel.callback(code="c")[1].endswith("/?oauth=erro")
    panel.google.status = 400
    assert panel.callback(code="c", state=panel.state())[1].endswith("gmail=erro")
    assert panel.google.calls  # the fake, never Google, was asked


def test_callback_for_a_purpose_without_handler_fails_softly(
    panel: Panel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(oauth._callbacks, "login", raising=False)  # phase 3 registers one
    url = authorization_url("login", user_id=None, invite_token=None, nonce="browser-nonce")
    panel.client.cookies.set(NONCE_COOKIE, "browser-nonce")
    state = parse_qs(urlparse(url).query)["state"][0]
    assert panel.callback(code="c", state=state)[1].endswith("/?oauth=erro")


def test_callback_dispatches_to_registered_handler(panel: Panel) -> None:
    from fastapi.responses import PlainTextResponse

    seen: list[str] = []

    def handler(state: oauth.OAuthState, identity: oauth.GoogleIdentity, deps: object) -> PlainTextResponse:
        seen.append(f"{state.purpose}:{identity.email}")
        return PlainTextResponse("ok")

    oauth.register_callback("login", handler)
    try:
        url = authorization_url("login", user_id=None, invite_token=None, nonce="browser-nonce")
        panel.client.cookies.set(NONCE_COOKIE, "browser-nonce")
        state = parse_qs(urlparse(url).query)["state"][0]
        r = panel.client.get("/api/oauth/google/callback", params={"code": "c", "state": state})
        assert (r.status_code, r.text, seen) == (200, "ok", ["login:me@example.com"])
    finally:
        oauth._callbacks.pop("login", None)


# --- webhook routed by emailAddress --------------------------------------------

def push(email: str) -> dict[str, dict[str, str]]:
    data = json.dumps({"emailAddress": email, "historyId": "1"})
    return {"message": {"data": base64.b64encode(data.encode()).decode()}}


def webhook(sf: sessionmaker[Session]) -> tuple[TestClient, list[int]]:
    ran: list[int] = []
    syncs = SyncCoordinators(ran.append)
    app = create_app(
        token=None, syncs=syncs, resolve_account=lambda email: account_id_by_email(sf, email)
    )
    return TestClient(app), ran


def test_push_for_a_triggers_only_a(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    client, ran = webhook(session_factory)
    with client:
        assert client.post("/webhooks/gmail", json=push("me@example.com")).status_code == 204
        assert ran == [1]
        assert client.post("/webhooks/gmail", json=push("Other@Example.com")).status_code == 204
        assert ran == [1, second_account]


def test_push_for_unknown_or_disconnected_account_is_204_and_ignored(
    session_factory: sessionmaker[Session],
) -> None:
    add_account(session_factory, 3, "gone@example.com")
    mark_status(session_factory, 3, "disconnected", None)
    client, ran = webhook(session_factory)
    with client:
        assert client.post("/webhooks/gmail", json=push("nobody@example.com")).status_code == 204
        assert client.post("/webhooks/gmail", json=push("gone@example.com")).status_code == 204
    assert ran == []
