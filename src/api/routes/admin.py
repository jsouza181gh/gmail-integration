"""/api/admin: invites, operational health per account, 2FA reset, platform events.

Admin only (403 for everyone else). Nothing here returns mail or chat content:
accounts show states and counts, events are the platform's own.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from src.accounts.health import AccountHealth, account_health
from src.api.deps import get_deps, require_admin
from src.api.routes.security import EventOut
from src.db.models import Invite, SecurityEvent
from src.llm.types import Schema

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)], tags=["admin"])

InviteState = Literal["pending", "accepted", "revoked"]


class InviteOut(Schema):
    id: int
    email: str
    state: InviteState
    created_at: datetime


def invite_state(row: Invite) -> InviteState:
    if row.revoked_at is not None:
        return "revoked"
    return "accepted" if row.accepted_at is not None else "pending"


@router.get("/accounts")
def accounts(request: Request) -> list[AccountHealth]:
    return account_health(get_deps(request).sf)


@router.post("/users/{user_id}/reset-2fa", status_code=204)
def reset_2fa(user_id: int, request: Request, admin_id: int = Depends(require_admin)) -> None:
    if not get_deps(request).auth.reset_totp(user_id, admin_id):
        raise HTTPException(404, "usuário não encontrado")


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: int, request: Request) -> None:
    deps = get_deps(request)
    with deps.sf.begin() as s:
        row = s.get(Invite, invite_id)
        if row is None:
            raise HTTPException(404, "convite não encontrado")
        if row.accepted_at is not None:
            raise HTTPException(409, "esse convite já foi usado")
        if row.revoked_at is None:
            row.revoked_at = deps.clock()


@router.get("/security/events")
def platform_events(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[EventOut]:
    """IP blocks, global lock and other events that belong to no single user."""
    with get_deps(request).sf() as s:
        return [
            EventOut(id=e.id, kind=e.kind, ip=e.ip, detail=e.detail, created_at=e.created_at)
            for e in s.scalars(
                select(SecurityEvent).where(SecurityEvent.user_id.is_(None))
                .order_by(SecurityEvent.id.desc()).limit(limit)
            )
        ]
