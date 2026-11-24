"""/api/security and /api/notifications: read-only views (unlocks are CLI-only)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from src.api.deps import get_deps, require_user
from src.db.models import Notification, SecurityEvent
from src.llm.types import Schema

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)], tags=["security"])


class EventOut(Schema):
    id: int
    kind: str
    ip: str | None
    detail: str
    created_at: datetime


class BlockOut(Schema):
    ip: str
    reason: str
    since: datetime


class BlocksOut(Schema):
    global_lock: bool
    ips: list[BlockOut]


class NotificationOut(Schema):
    id: int
    kind: str
    subject: str
    status: str
    error: str | None
    created_at: datetime
    sent_at: datetime | None


@router.get("/security/events")
def events(
    request: Request, kind: str | None = None, limit: int = Query(200, ge=1, le=1000)
) -> list[EventOut]:
    stmt = select(SecurityEvent)
    if kind:
        stmt = stmt.where(SecurityEvent.kind == kind)
    with get_deps(request).sf() as s:
        return [
            EventOut(id=e.id, kind=e.kind, ip=e.ip, detail=e.detail, created_at=e.created_at)
            for e in s.scalars(stmt.order_by(SecurityEvent.id.desc()).limit(limit))
        ]


@router.get("/security/blocks")
def blocks(request: Request) -> BlocksOut:
    auth = get_deps(request).auth
    return BlocksOut(
        global_lock=bool(auth.locked_users()),  # "some user is locked out"
        ips=[BlockOut(ip=ip, reason=reason, since=since) for ip, reason, since in auth.blocks()],
    )


@router.get("/notifications")
def notifications(request: Request, limit: int = Query(100, ge=1, le=500)) -> list[NotificationOut]:
    with get_deps(request).sf() as s:
        return [
            NotificationOut(id=n.id, kind=n.kind, subject=n.subject, status=n.status,
                            error=n.error, created_at=n.created_at, sent_at=n.sent_at)
            for n in s.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit))
        ]
