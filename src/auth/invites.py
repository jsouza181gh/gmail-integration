"""STUB(fase 3): substituído no merge. Convites por link (o João copia e manda)."""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.crypto import new_token, token_hash
from src.db.models import Invite
from src.llm.types import Schema


class InviteRow(Schema):
    id: int
    email: str


def create_invite(sf: sessionmaker[Session], email: str, created_by: int) -> str:
    """Link to give the invitee. A revoked invite is re-issued; an open one is an error."""
    email = email.strip().lower()
    raw = new_token()
    now = datetime.now(UTC)
    with sf.begin() as s:
        row = s.scalars(select(Invite).where(Invite.email == email)).first()
        if row is None:
            s.add(Invite(email=email, token_hash=token_hash(raw), created_by=created_by,
                         created_at=now))
        elif row.revoked_at is None:
            raise ValueError("esse e-mail já tem convite")
        else:
            row.token_hash, row.created_by, row.created_at = token_hash(raw), created_by, now
            row.revoked_at = row.accepted_at = None
    return f"/entrar?convite={raw}"


def invite_for_token(sf: sessionmaker[Session], raw: str) -> InviteRow | None:
    with sf() as s:
        row = s.scalars(
            select(Invite).where(Invite.token_hash == token_hash(raw), Invite.revoked_at.is_(None),
                                 Invite.accepted_at.is_(None))
        ).first()
        return InviteRow(id=row.id, email=row.email) if row else None


def accept(sf: sessionmaker[Session], invite_id: int) -> None:
    with sf.begin() as s:
        row = s.get(Invite, invite_id)
        if row is not None:
            row.accepted_at = datetime.now(UTC)
