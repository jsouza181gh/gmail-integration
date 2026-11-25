"""Ending a connection: stop the Gmail watch and revoke the token at Google.

Used when an account disconnects or is deleted (see `src/accounts/exit.py`).
"""
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import system_scope
from src.gmail.clients import GmailClients
from src.gmail.oauth import HttpForm, post_form
from src.platform.secrets import unseal

REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USER_ID = "me"


def stop_watch(clients: GmailClients, account_id: int) -> None:
    """Tell Gmail to stop pushing this mailbox's changes."""
    clients.service_for(account_id).users().stop(userId=USER_ID).execute(num_retries=3)


def revoke_token(
    sf: sessionmaker[Session], account_id: int, http: HttpForm = post_form
) -> None:
    """Revoke the refresh token at Google. A token Google already forgot is fine."""
    with system_scope(), sf() as s:
        sealed = s.get_one(GmailAccount, account_id).refresh_token_enc
    if sealed is None:
        return
    status, body = http(REVOKE_URL, {"token": unseal(sealed)})
    if status != 200 and "invalid_token" not in body:
        raise RuntimeError(f"o Google não revogou o token (HTTP {status})")
