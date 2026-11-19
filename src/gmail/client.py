
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

TOKEN_PATH = Path(
    os.getenv("GMAIL_TOKEN_PATH", "secrets/token.json")
)


def save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    temp_path = TOKEN_PATH.with_suffix(".tmp")

    temp_path.write_text(
        creds.to_json(),
        encoding="utf-8"
    )

    temp_path.replace(TOKEN_PATH)

    if os.name != "nt":
        TOKEN_PATH.chmod(0o600)


def get_gmail_service() -> Resource:
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            "Token OAuth não encontrado. "
            "Execute authorize.py para autorizar a conta."
        )

    creds = Credentials.from_authorized_user_file(
        str(TOKEN_PATH),
        SCOPES
    )

    if not creds.has_scopes(SCOPES):
        raise RuntimeError(
            "O token não possui as permissões necessárias."
        )

    if creds.expired:
        if not creds.refresh_token:
            raise RuntimeError(
                "Refresh token indisponível. "
                "É necessária uma nova autorização."
            )

        creds.refresh(Request())
        save_credentials(creds)

    if not creds.valid:
        raise RuntimeError(
            "Não foi possível obter credenciais válidas."
        )

    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False
    )
