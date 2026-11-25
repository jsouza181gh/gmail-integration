"""Leaving: disconnect Gmail (data stays) or delete the account (data goes).

`delete_account` runs in system scope in one transaction. The tables are not
listed by hand: every table with a `gmail_account_id` column is cleaned, in
foreign-key order (children first) taken from the ORM metadata, so a table
added later is covered automatically. Invites are not touched: deleting an
account does not give the person a way back in without a new invite.

Google failures (stop watch, revoke token) are logged and never block either
action: the person wants out, and a revoked or unreachable token must not trap
their data.
"""
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.accounts import account_for_user
from src.db.models import Base, GmailAccount, User
from src.db.scope import system_scope
from src.gmail.accounts_store import mark_status
from src.gmail.clients import GmailClients
from src.gmail.lifecycle import revoke_token, stop_watch
from src.utils.console import log

# deploy/deploy.sh keeps this many database dumps (BACKUPS_KEPT); a test keeps both in step
BACKUP_COPIES_KEPT = 7


class AccountProtected(RuntimeError):
    """The admin account cannot be deleted through the panel."""


def account_tables() -> list[str]:
    """Names of every table with rows of one Gmail account, children before parents."""
    return [
        t.name for t in reversed(Base.metadata.sorted_tables)
        if "gmail_account_id" in t.c
    ]


def user_tables() -> list[str]:
    """Names of the platform tables whose rows hang from a user (sessions, recovery codes...).

    Only a `user_id` FK counts: `invites.created_by` also points at users but
    belongs to the inviter, and the invite is kept (coming back needs a new one)."""
    users = Base.metadata.tables["users"]
    return [
        t.name for t in reversed(Base.metadata.sorted_tables)
        if t is not users and "user_id" in t.c
        and any(fk.column.table is users for fk in t.c.user_id.foreign_keys)
    ]


def _release_google(sf: sessionmaker[Session], clients: GmailClients, account_id: int) -> None:
    steps: list[tuple[str, Callable[[], None]]] = [
        ("parar o watch", lambda: stop_watch(clients, account_id)),
        ("revogar o token", lambda: revoke_token(sf, account_id)),
    ]
    for what, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - Google being down must not block leaving
            log(f"conta {account_id}: falhou ao {what}: {type(exc).__name__}: {exc}")


def disconnect(sf: sessionmaker[Session], clients: GmailClients, account_id: int) -> None:
    """Stop watching, revoke the token and mark the account `disconnected`.
    Data and `history_id` stay: reconnecting resumes from where it stopped."""
    _release_google(sf, clients, account_id)
    mark_status(sf, account_id, "disconnected", None)


def delete_account(sf: sessionmaker[Session], clients: GmailClients, user_id: int) -> None:
    """Release Google, then erase the account and the user in one transaction."""
    account_id = account_for_user(sf, user_id)
    with system_scope(), sf() as s:
        user = s.get(User, user_id)
        if user is not None and user.role == "admin":
            raise AccountProtected("a conta do administrador não pode ser excluída pelo painel")
    if account_id is not None:
        _release_google(sf, clients, account_id)
    with system_scope(), sf.begin() as s:
        if account_id is not None:
            for name in account_tables():
                table = Base.metadata.tables[name]
                s.execute(delete(table).where(table.c.gmail_account_id == account_id))
            s.execute(delete(GmailAccount).where(GmailAccount.id == account_id))
        for name in user_tables():
            table = Base.metadata.tables[name]
            s.execute(delete(table).where(table.c.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
    log(f"conta {account_id} (usuário {user_id}) excluída")


def account_email(sf: sessionmaker[Session], user_id: int) -> str | None:
    with system_scope(), sf() as s:
        return s.scalar(select(GmailAccount.email_address).where(GmailAccount.user_id == user_id))
