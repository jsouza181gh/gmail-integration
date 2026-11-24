"""Shared helpers: an APP_SECRET, connected accounts, a fake Google token endpoint."""
import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session, sessionmaker

from src.gmail.accounts_store import save_connection
from src.gmail.oauth import GMAIL_SCOPES, GoogleIdentity
from src.gmail.oauth_clients import upsert_client

APP_SECRET = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def app_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Import this fixture into a test module to get an APP_SECRET."""
    monkeypatch.setenv("APP_SECRET", APP_SECRET)
    return APP_SECRET


def connect(
    sf: sessionmaker[Session], account_id: int, email: str, refresh_token: str = "rt-1",
    scopes: list[str] | None = None, kind: str = "platform",
) -> int:
    """Give `account_id` a sealed refresh token; returns the oauth client id."""
    client = upsert_client(sf, kind, "cid.apps.googleusercontent.com", "csecret")
    save_connection(
        sf, account_id,
        GoogleIdentity(email=email, email_verified=True, sub="sub", refresh_token=refresh_token,
                       scopes=scopes if scopes is not None else GMAIL_SCOPES),
        client,
    )
    return client


class FakeRefresher:
    """Stands in for the HTTP refresh: records the refresh token it was asked
    to use, can fail with invalid_grant or rotate the refresh token."""

    def __init__(self) -> None:
        self.used: list[str | None] = []
        self.fail: str | None = None
        self.rotate_to: str | None = None

    def __call__(self, creds: Credentials) -> None:
        self.used.append(creds.refresh_token)
        if self.fail:
            raise RefreshError(self.fail)
        creds.token = f"access-{len(self.used)}"
        creds.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)  # google-auth uses naive UTC
        if self.rotate_to:
            creds._refresh_token = self.rotate_to


def id_token(email: str, aud: str, sub: str = "sub-1", verified: bool = True) -> str:
    def part(obj: Mapping[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claims = {"email": email, "email_verified": verified, "sub": sub, "aud": aud}
    return f"{part({'alg': 'none'})}.{part(claims)}."


class FakeGoogle:
    """`HttpForm` for tests: answers the token endpoint, records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.status = 200
        self.email = "me@example.com"
        self.refresh_token: str | None = "rt-from-google"
        self.scope = " ".join(GMAIL_SCOPES)
        self.verified = True
        self.aud: str | None = None

    def __call__(self, url: str, data: dict[str, str]) -> tuple[int, str]:
        self.calls.append((url, data))
        if self.status != 200:
            return self.status, '{"error":"invalid_grant"}'
        body: dict[str, str] = {
            "access_token": "at",
            "id_token": id_token(self.email, self.aud or data["client_id"], verified=self.verified),
            "scope": self.scope,
        }
        if self.refresh_token:
            body["refresh_token"] = self.refresh_token
        return 200, json.dumps(body)
