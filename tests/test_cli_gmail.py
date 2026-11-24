"""Bridge CLI (legacy Desktop token) and the platform OAuth client sync."""
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.cli.gmail_import_token import import_token
from src.cli.oauth_client import sync_platform_client
from src.db.models import GmailAccount, OAuthClient
from src.db.scope import system_scope
from src.gmail.clients import GmailClients
from src.gmail.oauth_clients import platform_client
from src.platform.secrets import unseal
from tests.oauth_fakes import FakeRefresher, app_secret  # noqa: F401

pytestmark = pytest.mark.noscope

TOKEN = {
    "token": "ya29.old",
    "refresh_token": "1//legacy-refresh",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "desktop-id.apps.googleusercontent.com",
    "client_secret": "desktop-secret",
    "scopes": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.settings.basic",
    ],
}
CREDENTIALS = {
    "installed": {"client_id": "desktop-id.apps.googleusercontent.com", "client_secret": "from-credentials"}
}


def write(path: Path, data: Mapping[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_import_token_makes_the_account_usable(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    token = write(tmp_path / "token.json", TOKEN)
    creds = write(tmp_path / "credentials.json", CREDENTIALS)
    assert import_token(session_factory, "Me@Example.com", token, creds) == 1
    with system_scope(), session_factory() as s:
        acc = s.get_one(GmailAccount, 1)
        client = s.get_one(OAuthClient, acc.oauth_client_id)
        assert client.kind == "desktop" and unseal(client.client_secret_enc) == "from-credentials"
        assert acc.refresh_token_enc and unseal(acc.refresh_token_enc) == "1//legacy-refresh"
        assert acc.status == "connected" and acc.scopes is not None
        assert "gmail.settings.basic" in acc.scopes
    refresher = FakeRefresher()
    GmailClients(session_factory, refresh=refresher, build_service=lambda c: object()).service_for(1)
    assert refresher.used == ["1//legacy-refresh"]


def test_import_token_without_credentials_file_uses_the_token_client(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    token = write(tmp_path / "token.json", TOKEN)
    import_token(session_factory, "me@example.com", token, tmp_path / "missing.json")
    with system_scope(), session_factory() as s:
        client = s.query(OAuthClient).one()
        assert unseal(client.client_secret_enc) == "desktop-secret"


def test_import_token_errors_are_readable(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    token = write(tmp_path / "token.json", TOKEN)
    creds = tmp_path / "none.json"
    with pytest.raises(SystemExit, match="conta não encontrada: x@y.z"):
        import_token(session_factory, "x@y.z", token, creds)
    with pytest.raises(SystemExit, match="token ilegível"):
        import_token(session_factory, "me@example.com", tmp_path / "absent.json", creds)
    bad = write(tmp_path / "bad.json", {"foo": 1})
    with pytest.raises(SystemExit, match="token ilegível"):
        import_token(session_factory, "me@example.com", bad, creds)
    no_client = write(tmp_path / "noclient.json", {"refresh_token": "r"})
    with pytest.raises(SystemExit, match="client_id"):
        import_token(session_factory, "me@example.com", no_client, creds)


def test_oauth_client_sync_reads_env(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(SystemExit):
        sync_platform_client(session_factory)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-secret")
    first = sync_platform_client(session_factory)
    assert sync_platform_client(session_factory) == first
    found = platform_client(session_factory)
    assert found is not None and (found.client_id, found.client_secret) == ("web-id", "web-secret")
