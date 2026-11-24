import base64
import hashlib
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.gmail import oauth
from src.gmail.oauth import (
    GMAIL_SCOPES,
    LOGIN_SCOPES,
    OAuthError,
    OAuthNotConfigured,
    OAuthState,
    authorization_url,
    exchange,
    read_state,
)
from src.gmail.oauth_clients import upsert_client
from src.platform.secrets import seal
from tests.oauth_fakes import FakeGoogle, app_secret  # noqa: F401

ORIGIN = "https://panel.test"


@pytest.fixture
def google(session_factory: sessionmaker[Session]) -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    upsert_client(session_factory, "platform", "cid.apps.googleusercontent.com", "csecret")
    oauth.configure(session_factory, ORIGIN, fake)
    yield fake
    oauth._runtime = None


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_connect_url_asks_gmail_scopes_offline_with_pkce(google: FakeGoogle) -> None:
    url = authorization_url("connect", user_id=7, invite_token=None)
    q = query(url)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["scope"].split() == GMAIL_SCOPES
    assert (q["access_type"], q["prompt"], q["response_type"]) == ("offline", "consent", "code")
    assert q["redirect_uri"] == f"{ORIGIN}/api/oauth/google/callback"
    assert q["client_id"] == "cid.apps.googleusercontent.com"
    assert q["code_challenge_method"] == "S256"
    state = read_state(q["state"])
    assert (state.purpose, state.user_id) == ("connect", 7)
    digest = hashlib.sha256(state.verifier.encode()).digest()
    assert q["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert state.exp - int(time.time()) in range(595, 601)


def test_login_url_asks_only_identity(google: FakeGoogle) -> None:
    q = query(authorization_url("login", user_id=None, invite_token=None))
    assert q["scope"].split() == LOGIN_SCOPES
    assert q["prompt"] == "select_account" and "access_type" not in q


def test_state_is_encrypted(google: FakeGoogle) -> None:
    q = query(authorization_url("signup", user_id=None, invite_token="tok-123"))
    assert "tok-123" not in q["state"] and "verifier" not in q["state"]
    assert read_state(q["state"]).invite_token == "tok-123"


def test_forged_and_expired_states_are_refused(google: FakeGoogle) -> None:
    with pytest.raises(OAuthError):
        read_state("lixo")
    old = OAuthState(purpose="connect", user_id=1, verifier="v", invite_token=None,
                     exp=int(time.time()) - 1)
    with pytest.raises(OAuthError):
        read_state(seal(old.model_dump_json()))
    with pytest.raises(OAuthError):
        read_state(seal('{"purpose": "admin"}'))


def test_not_configured_without_platform_client(session_factory: sessionmaker[Session]) -> None:
    oauth.configure(session_factory, ORIGIN, FakeGoogle())
    try:
        with pytest.raises(OAuthNotConfigured):
            authorization_url("connect", user_id=1, invite_token=None)
    finally:
        oauth._runtime = None


def test_exchange_returns_identity_and_sends_the_verifier(google: FakeGoogle) -> None:
    state_raw = query(authorization_url("connect", user_id=3, invite_token=None))["state"]
    state, identity = exchange("the-code", state_raw)
    assert state.user_id == 3
    assert identity.email == "me@example.com" and identity.email_verified
    assert identity.refresh_token == "rt-from-google"
    assert identity.scopes == GMAIL_SCOPES
    url, form = google.calls[0]
    assert url == "https://oauth2.googleapis.com/token"
    assert form["code"] == "the-code" and form["code_verifier"] == state.verifier
    assert form["client_secret"] == "csecret"
    assert form["redirect_uri"] == f"{ORIGIN}/api/oauth/google/callback"


def test_exchange_lowercases_email_and_may_have_no_refresh_token(google: FakeGoogle) -> None:
    google.email, google.refresh_token = "Me@Example.COM", None
    _, identity = exchange("c", query(authorization_url("connect", user_id=1, invite_token=None))["state"])
    assert identity.email == "me@example.com" and identity.refresh_token is None


def test_exchange_refuses_google_errors_and_foreign_audience(google: FakeGoogle) -> None:
    state_raw = query(authorization_url("connect", user_id=1, invite_token=None))["state"]
    google.status = 400
    with pytest.raises(OAuthError):
        exchange("c", state_raw)
    google.status, google.aud = 200, "another-app"
    with pytest.raises(OAuthError):
        exchange("c", state_raw)
