"""Gmail connection state of the accounts (system scope: ids and metadata only,
never mail content)."""
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount
from src.db.scope import system_scope
from src.gmail.oauth import GoogleIdentity
from src.platform.secrets import seal

CONNECTED = "connected"
REVOKED = "revoked"
DISCONNECTED = "disconnected"
MAX_ERROR = 300


def save_connection(
    sf: sessionmaker[Session], account_id: int, identity: GoogleIdentity, oauth_client_id: int
) -> None:
    """Store the (sealed) refresh token and granted scopes; the account is connected."""
    if identity.refresh_token is None:
        raise ValueError("a conexão precisa de um refresh token")
    with system_scope(), sf.begin() as s:
        acc = s.get_one(GmailAccount, account_id)
        acc.oauth_client_id = oauth_client_id
        acc.refresh_token_enc = seal(identity.refresh_token)
        acc.scopes = " ".join(identity.scopes)
        acc.status = CONNECTED
        acc.last_error = None
        acc.connected_at = datetime.now(UTC)


def save_refresh_token(sf: sessionmaker[Session], account_id: int, refresh_token: str) -> None:
    """Google rotated the refresh token: keep the new one."""
    with system_scope(), sf.begin() as s:
        s.get_one(GmailAccount, account_id).refresh_token_enc = seal(refresh_token)


def account_email(sf: sessionmaker[Session], account_id: int) -> str | None:
    with system_scope(), sf() as s:
        return s.scalar(select(GmailAccount.email_address).where(GmailAccount.id == account_id))


def account_id_by_email(sf: sessionmaker[Session], email: str) -> int | None:
    """The CONNECTED account of this address (anything else: None)."""
    with system_scope(), sf() as s:
        return s.scalar(
            select(GmailAccount.id).where(
                GmailAccount.email_address == email.strip().lower(),
                GmailAccount.status == CONNECTED,
            )
        )


def mark_status(
    sf: sessionmaker[Session], account_id: int, status: str, error: str | None
) -> None:
    with system_scope(), sf.begin() as s:
        acc = s.get_one(GmailAccount, account_id)
        acc.status = status
        acc.last_error = error[:MAX_ERROR] if error else None


def mark_synced(sf: sessionmaker[Session], account_id: int, at: datetime) -> None:
    with system_scope(), sf.begin() as s:
        acc = s.get_one(GmailAccount, account_id)
        acc.last_sync_at = at
        acc.last_error = None


def syncable_account_ids(sf: sessionmaker[Session]) -> list[int]:
    """Connected accounts that hold a token (the ones with Gmail access)."""
    with system_scope(), sf() as s:
        return list(s.scalars(
            select(GmailAccount.id)
            .where(GmailAccount.status == CONNECTED, GmailAccount.refresh_token_enc.is_not(None))
            .order_by(GmailAccount.id)
        ))


def accounts_needing_renewal(
    sf: sessionmaker[Session], now_ms: int, within_ms: int
) -> list[int]:
    """Connected accounts with a token whose watch expires within `within_ms`
    (or never started)."""
    with system_scope(), sf() as s:
        return list(s.scalars(
            select(GmailAccount.id)
            .where(
                GmailAccount.status == CONNECTED,
                GmailAccount.refresh_token_enc.is_not(None),
                or_(
                    GmailAccount.watch_expiration.is_(None),
                    GmailAccount.watch_expiration < now_ms + within_ms,
                ),
            )
            .order_by(GmailAccount.id)
        ))
