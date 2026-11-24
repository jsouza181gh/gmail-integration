"""/api/admin/invites: the admin registers an e-mail and copies the link (UI: phase 5)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import get_deps, request_ip, require_admin
from src.auth.invites import InviteError, InviteRow, create_invite, list_invites, revoke
from src.llm.types import Schema

router = APIRouter(prefix="/api/admin/invites", tags=["admin"], dependencies=[Depends(require_admin)])


class InviteIn(Schema):
    email: str


class InviteOut(Schema):
    id: int
    email: str
    status: str
    created_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class CreatedInviteOut(Schema):
    link: str
    invite: InviteOut


def _out(row: InviteRow) -> InviteOut:
    return InviteOut(id=row.id, email=row.email, status=row.status, created_at=row.created_at,
                     accepted_at=row.accepted_at, revoked_at=row.revoked_at)


@router.post("")
def create(body: InviteIn, request: Request, admin_id: int = Depends(require_admin)) -> CreatedInviteOut:
    deps = get_deps(request)
    try:
        link = create_invite(deps.sf, body.email[:320], admin_id, now=deps.clock(),
                             origin=deps.settings.origin)
    except InviteError as exc:
        raise HTTPException(400, str(exc)) from exc
    deps.log.record("invite_created", request_ip(request), f"convite para {body.email.strip().lower()[:200]}")
    row = next(i for i in list_invites(deps.sf) if i.email == body.email.strip().lower())
    return CreatedInviteOut(link=link, invite=_out(row))


@router.get("")
def listing(request: Request) -> list[InviteOut]:
    return [_out(i) for i in list_invites(get_deps(request).sf)]


@router.delete("/{invite_id}", status_code=204)
def revoke_invite(invite_id: int, request: Request) -> None:
    deps = get_deps(request)
    if not revoke(deps.sf, invite_id, now=deps.clock()):
        raise HTTPException(404, "convite não encontrado ou já usado")
