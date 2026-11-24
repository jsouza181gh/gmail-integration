"""Platform lookups across accounts (system scope). They return ids only,
never content."""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount, User
from src.db.scope import system_scope


def connected_account_ids(sf: sessionmaker[Session]) -> list[int]:
    with system_scope(), sf() as s:
        return list(s.scalars(
            select(GmailAccount.id).where(GmailAccount.status == "connected").order_by(GmailAccount.id)
        ))


def admin_account_id(sf: sessionmaker[Session]) -> int | None:
    """The account of the first admin user: where operational alerts go."""
    with system_scope(), sf() as s:
        return s.scalar(
            select(GmailAccount.id)
            .join(User, User.id == GmailAccount.user_id)
            .where(User.role == "admin")
            .order_by(User.id)
            .limit(1)
        )


def account_for_user(sf: sessionmaker[Session], user_id: int) -> int | None:
    with system_scope(), sf() as s:
        return s.scalar(select(GmailAccount.id).where(GmailAccount.user_id == user_id))


def account_by_email(sf: sessionmaker[Session], email_address: str) -> int | None:
    with system_scope(), sf() as s:
        return s.scalar(
            select(GmailAccount.id).where(GmailAccount.email_address == email_address.strip().lower())
        )
