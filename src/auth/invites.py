"""Invites: the admin registers an e-mail, copies the link, the friend enters.

One invite per e-mail. The raw token only exists in the link (the table keeps
its SHA-256). `open_invite_for` is the single entry check: opening
registration later means changing only that function.
"""
import os
import re
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.crypto import new_token, token_hash
from src.db.models import Invite, User
from src.llm.types import Schema

InviteStatus = Literal["open", "accepted", "revoked"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InviteError(ValueError):
    """The request cannot be honoured; the message is meant for the admin."""


class InviteRow(Schema):
    id: int
    email: str
    created_by: int
    created_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None

    @property
    def status(self) -> InviteStatus:
        if self.accepted_at is not None:
            return "accepted"
        return "revoked" if self.revoked_at is not None else "open"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _row(i: Invite) -> InviteRow:
    return InviteRow(id=i.id, email=i.email, created_by=i.created_by, created_at=i.created_at,
                     accepted_at=i.accepted_at, revoked_at=i.revoked_at)


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not _EMAIL.match(cleaned):
        raise InviteError(f"e-mail inválido: {email.strip()[:80]!r}")
    return cleaned


def invite_link(raw: str, origin: str | None = None) -> str:
    base = (origin if origin is not None else os.getenv("APP_ORIGIN", "")).rstrip("/")
    return f"{base}/entrar?convite={raw}"


def create_invite(
    sf: sessionmaker[Session], email: str, created_by: int,
    *, now: datetime | None = None, origin: str | None = None,
) -> str:
    """Create (or re-issue) the invite for `email`; returns the link to hand over.

    Re-inviting an open or revoked e-mail issues a fresh token (the old link
    dies). An accepted invite is re-opened only when its user is gone
    (deleted account: coming back needs a new invite).
    """
    address = normalize_email(email)
    when = now or _utcnow()
    raw = new_token()
    with sf.begin() as s:
        invite = s.scalars(select(Invite).where(Invite.email == address)).first()
        if invite is None:
            s.add(Invite(email=address, token_hash=token_hash(raw), created_by=created_by,
                         created_at=when))
        else:
            if invite.accepted_at is not None and s.scalars(
                select(User.id).where(User.email == address)
            ).first() is not None:
                raise InviteError(f"{address} já tem conta")
            invite.token_hash = token_hash(raw)
            invite.created_by, invite.created_at = created_by, when
            invite.accepted_at = invite.revoked_at = None
    return invite_link(raw, origin)


def invite_for_token(sf: sessionmaker[Session], raw: str) -> InviteRow | None:
    """The open invite the link belongs to (accepted or revoked ones do not open)."""
    if not raw:
        return None
    with sf() as s:
        invite = s.scalars(select(Invite).where(Invite.token_hash == token_hash(raw))).first()
        if invite is None or invite.accepted_at is not None or invite.revoked_at is not None:
            return None
        return _row(invite)


def open_invite_for(sf: sessionmaker[Session], email: str, raw_token: str | None) -> InviteRow | None:
    """THE entry check: may this verified e-mail create an account right now?

    To open registration to everyone, change only this function.
    """
    if not raw_token:
        return None
    invite = invite_for_token(sf, raw_token)
    if invite is None or invite.email != email.strip().lower():
        return None
    return invite


def accept(sf: sessionmaker[Session], invite_id: int, *, now: datetime | None = None) -> None:
    with sf.begin() as s:
        invite = s.get(Invite, invite_id)
        if invite is not None and invite.accepted_at is None:
            invite.accepted_at = now or _utcnow()


def revoke(sf: sessionmaker[Session], invite_id: int, *, now: datetime | None = None) -> bool:
    """True when an open invite was revoked."""
    with sf.begin() as s:
        invite = s.get(Invite, invite_id)
        if invite is None or invite.accepted_at is not None or invite.revoked_at is not None:
            return False
        invite.revoked_at = now or _utcnow()
        return True


def list_invites(sf: sessionmaker[Session]) -> list[InviteRow]:
    with sf() as s:
        return [_row(i) for i in s.scalars(select(Invite).order_by(Invite.id.desc()))]
