"""Which Gmail account the current code runs for.

Every table holding a user's content mixes in `AccountOwned`. Inside
`account_scope(id)` the ORM only sees and writes that account's rows (and on
Postgres the session sets `app.account_id` for the row-level security
policies). `system_scope()` is the short, listed set of platform paths that
work across accounts (job claim, webhook routing, watch renewal, admin
lookups). Touching an account table with no scope raises `ScopeError`: a
forgotten entry point fails loudly instead of reading everything.

The scope lives in a ContextVar, so a new thread starts with none.
"""
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Literal

from sqlalchemy import ForeignKey, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    SessionTransaction,
    declared_attr,
    mapped_column,
    sessionmaker,
    with_loader_criteria,
)

type Scope = int | Literal["system"] | None

_scope: ContextVar[Scope] = ContextVar("account_scope", default=None)

APP_ROLE = "gi_app"  # Postgres role the row-level security policies bind (see 0021)


class ScopeError(RuntimeError):
    pass


class AccountOwned:
    """Mixin: the row belongs to one Gmail account."""

    @declared_attr
    def gmail_account_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("gmail_accounts.id"), nullable=False, index=True)


@contextmanager
def _set(value: Scope) -> Iterator[None]:
    token = _scope.set(value)
    try:
        yield
    finally:
        _scope.reset(token)


def account_scope(account_id: int) -> AbstractContextManager[None]:
    return _set(account_id)


def system_scope() -> AbstractContextManager[None]:
    return _set("system")


def in_system_scope() -> bool:
    return _scope.get() == "system"


def current_account() -> int:
    value = _scope.get()
    if not isinstance(value, int):
        raise ScopeError("nenhuma conta no escopo atual")
    return value


def email_key(email_id: str) -> tuple[int, str]:
    """Primary key of an `emails` row in the current account."""
    return (current_account(), email_id)


def _touches_owned(state: ORMExecuteState) -> bool:
    return any(issubclass(m.class_, AccountOwned) for m in state.all_mappers)


def install_scoping(sf: sessionmaker[Session]) -> None:
    @event.listens_for(sf, "do_orm_execute")
    def _criteria(state: ORMExecuteState) -> None:
        if not (state.is_select or state.is_update or state.is_delete):
            return
        if not _touches_owned(state):
            return
        scope = _scope.get()
        if scope == "system":
            return
        if scope is None:
            raise ScopeError("acesso a dados de conta sem escopo de conta")
        account_id = scope
        state.statement = state.statement.options(
            with_loader_criteria(
                AccountOwned,
                lambda cls: cls.gmail_account_id == account_id,
                include_aliases=True,
            )
        )

    @event.listens_for(sf, "before_flush")
    def _fill(session: Session, _ctx: object, _instances: object) -> None:
        for obj in session.new:
            if isinstance(obj, AccountOwned) and getattr(obj, "gmail_account_id", None) is None:
                obj.gmail_account_id = current_account()

    @event.listens_for(sf, "after_begin")
    def _pg_settings(session: Session, _tx: SessionTransaction, conn: Connection) -> None:
        if conn.dialect.name != "postgresql":
            return
        # the owner/superuser bypasses RLS: every app session runs as the
        # NOLOGIN role the policies apply to (created by migration 0021)
        conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        scope = _scope.get()
        if scope == "system":
            conn.execute(text("SELECT set_config('app.system', 'on', true)"))
        elif isinstance(scope, int):
            conn.execute(
                text("SELECT set_config('app.account_id', :v, true)"), {"v": str(scope)}
            )
