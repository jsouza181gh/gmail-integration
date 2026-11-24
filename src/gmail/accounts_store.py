"""Platform-side Gmail account rows (system scope; ids and metadata only)."""
# STUB(fase 2): substituído no merge
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import system_scope
from src.gmail.oauth import GoogleIdentity


def save_connection(
    sf: sessionmaker[Session], account_id: int, identity: GoogleIdentity, oauth_client_id: int
) -> None:
    with system_scope(), sf.begin() as s:
        s.execute(update(GmailAccount).where(GmailAccount.id == account_id).values(status="connected"))
