"""STUB(fase 2): substituído no merge. Only `mark_status` (fase 6 needs it)."""
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import system_scope


def mark_status(sf: sessionmaker[Session], account_id: int, status: str, error: str | None) -> None:
    # the real one also stores `error` in gmail_accounts.last_error (0022)
    with system_scope(), sf.begin() as s:
        s.execute(update(GmailAccount).where(GmailAccount.id == account_id).values(status=status))
