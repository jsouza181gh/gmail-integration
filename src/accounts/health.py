"""Operational health per account for the admin: states and counts, never content.

Reads run in system scope; nothing here touches subjects, senders, bodies or
chat text, and the Gmail error is reduced to a short kind.
"""
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount, Job, User, UserLlmKey
from src.db.scope import system_scope
from src.llm.types import Schema

ErrorKind = Literal["revoked", "quota", "network", "other"]


class AccountHealth(Schema):
    user_id: int
    email: str
    role: str
    status: str
    last_sync_at: datetime | None
    watch_expires_at: datetime | None
    error_kind: ErrorKind | None
    queue_depth: int
    dead_jobs: int
    key_ok: bool | None  # None: no key saved yet


def error_kind(text: str | None) -> ErrorKind | None:
    if not text:
        return None
    low = text.lower()
    if "invalid_grant" in low or "revoked" in low or "revogad" in low:
        return "revoked"
    if "quota" in low or "429" in low or "rate" in low:
        return "quota"
    if any(w in low for w in ("timeout", "timed out", "connection", "503", "unavailable")):
        return "network"
    return "other"


def _counts(s: Session, statuses: tuple[str, ...]) -> dict[int, int]:
    rows = s.execute(
        select(Job.gmail_account_id, func.count())
        .where(Job.status.in_(statuses)).group_by(Job.gmail_account_id)
    )
    return {account: n for account, n in rows}


def account_health(sf: sessionmaker[Session]) -> list[AccountHealth]:
    with system_scope(), sf() as s:
        queued = _counts(s, ("queued", "running"))
        dead = _counts(s, ("dead",))
        keys = {k.user_id: k for k in s.scalars(select(UserLlmKey))}
        out: list[AccountHealth] = []
        for acc, user in s.execute(
            select(GmailAccount, User).join(User, User.id == GmailAccount.user_id)
            .order_by(GmailAccount.id)
        ):
            key = keys.get(user.id)
            out.append(AccountHealth(
                user_id=user.id, email=acc.email_address, role=user.role, status=acc.status,
                last_sync_at=acc.last_sync_at,
                watch_expires_at=(
                    datetime.fromtimestamp(acc.watch_expiration / 1000, UTC)
                    if acc.watch_expiration else None
                ),
                error_kind=error_kind(acc.last_error),
                queue_depth=queued.get(acc.id, 0), dead_jobs=dead.get(acc.id, 0),
                key_ok=None if key is None else key.last_error is None,
            ))
        return out
