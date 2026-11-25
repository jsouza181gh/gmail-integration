"""CLI: bridge for the first account. Imports the legacy Desktop token.

Uso (na VPS, depois do deploy da fase 2):
  docker compose exec api python -m src.cli.gmail_import_token --account voce@gmail.com

Lê secrets/token.json (ou GMAIL_TOKEN_PATH) e credentials.json (ou
GMAIL_CREDENTIALS_PATH), guarda o refresh token cifrado no banco e registra
o client Desktop em oauth_clients (kind='desktop'). Assim o Gmail continua
funcionando entre o deploy e a reconexão pelo painel.
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from src.db.accounts import account_by_email
from src.gmail.accounts_store import save_connection
from src.gmail.client import SCOPES
from src.gmail.oauth import GoogleIdentity
from src.gmail.oauth_clients import upsert_client


class _TokenFile(BaseModel):
    refresh_token: str
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] = []


class _ClientSection(BaseModel):
    client_id: str
    client_secret: str


class _CredentialsFile(BaseModel):
    installed: _ClientSection | None = None
    web: _ClientSection | None = None


def import_token(
    sf: sessionmaker[Session], email: str, token_path: Path, credentials_path: Path
) -> int:
    """Returns the account id. Raises SystemExit with a readable message."""
    account = account_by_email(sf, email)
    if account is None:
        raise SystemExit(f"conta não encontrada: {email.strip().lower()}")
    try:
        token = _TokenFile.model_validate_json(token_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise SystemExit(f"token ilegível em {token_path}: {type(exc).__name__}") from exc
    client_id, client_secret = token.client_id, token.client_secret
    if credentials_path.exists():
        try:
            creds = _CredentialsFile.model_validate_json(credentials_path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise SystemExit(f"credentials.json ilegível: {type(exc).__name__}") from exc
        section = creds.installed or creds.web
        if section is not None:
            client_id, client_secret = section.client_id, section.client_secret
    if not client_id or not client_secret:
        raise SystemExit("client_id/client_secret não encontrados (credentials.json ou token.json)")
    client = upsert_client(sf, "desktop", client_id, client_secret)
    save_connection(
        sf,
        account,
        GoogleIdentity(
            email=email.strip().lower(), email_verified=True, sub="",
            refresh_token=token.refresh_token, scopes=token.scopes or SCOPES,
        ),
        client,
    )
    return account


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    from src.db.store import Store

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--account", required=True, help="e-mail da conta que recebe o token")
    args = parser.parse_args(argv)
    store = Store()
    try:
        account = import_token(
            store.sf,
            args.account,
            Path(os.getenv("GMAIL_TOKEN_PATH", "secrets/token.json")),
            Path(os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")),
        )
    finally:
        store.close()
    print(f"token importado para a conta {account} ({args.account.strip().lower()})")
    sys.exit(0)


if __name__ == "__main__":
    main()
