"""STUB(fase 3): POST/GET /api/admin/invites. Substituído no merge pela rota da fase 3.

The revoke route (DELETE) belongs to phase 5 and lives in `admin.py`.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from src.api.deps import get_deps, require_admin
from src.api.routes.admin import InviteOut, invite_state
from src.auth.invites import create_invite
from src.db.models import Invite
from src.llm.types import Schema

router = APIRouter(prefix="/api/admin", tags=["admin"])


class InviteIn(Schema):
    email: str


class InviteCreated(Schema):
    link: str


@router.post("/invites", status_code=201)
def create(
    body: InviteIn, request: Request, admin_id: int = Depends(require_admin)
) -> InviteCreated:
    email = body.email.strip().lower()
    if "@" not in email or len(email) > 200:
        raise HTTPException(422, "Esse e-mail não parece certo.")
    try:
        return InviteCreated(link=create_invite(get_deps(request).sf, email, admin_id))
    except ValueError as exc:
        raise HTTPException(409, "Esse e-mail já tem um convite.") from exc


@router.get("/invites", dependencies=[Depends(require_admin)])
def listing(request: Request) -> list[InviteOut]:
    with get_deps(request).sf() as s:
        return [
            InviteOut(id=i.id, email=i.email, state=invite_state(i), created_at=i.created_at)
            for i in s.scalars(select(Invite).order_by(Invite.id.desc()))
        ]
