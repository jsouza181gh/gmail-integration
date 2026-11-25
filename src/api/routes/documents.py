"""/api/documents: indexed attachments; downloads fetched from Gmail on demand.

A download needs the session *and* a 60-second token bound to that one
attachment (issued by POST .../link), so a leaked URL is useless shortly
after and never opens another document. Every download is a security event.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import or_, select

from src.api import queries
from src.api.deps import get_deps, optional_user, request_ip, require_user
from src.auth.crypto import issue_download, read_download
from src.db.models import Attachment, Email
from src.gmail.full import fetch_attachment
from src.llm.types import Schema

router = APIRouter(prefix="/api/documents", dependencies=[Depends(require_user)], tags=["documents"])


class LinkOut(Schema):
    url: str
    expires_in: int


@router.get("")
def list_documents(
    request: Request,
    doc_class: str | None = Query(None, alias="class"),
    doc_type: str | None = Query(None, alias="type"),
    q: str | None = None,
    status: str = "done",
    before_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[queries.DocumentOut]:
    stmt = select(Attachment, Email).join(Email, Email.id == Attachment.email_id).where(
        Attachment.status == status
    )
    if doc_class:
        stmt = stmt.where(Attachment.doc_class == doc_class)
    if doc_type:
        stmt = stmt.where(Attachment.doc_type == doc_type)
    if q:
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Attachment.title.ilike(like), Attachment.issuer.ilike(like),
                              Attachment.filename.ilike(like)))
    if before_id is not None:
        stmt = stmt.where(Attachment.id < before_id)
    with get_deps(request).sf() as s:
        rows = s.execute(stmt.order_by(Attachment.id.desc()).limit(limit)).all()
        return [queries.document_out(a, e) for a, e in rows]


@router.post("/{attachment_id}/link")
def link(attachment_id: int, request: Request) -> LinkOut:
    deps = get_deps(request)
    if deps.index_store.attachment(attachment_id) is None:
        raise HTTPException(404, "documento não encontrado")
    token = issue_download(attachment_id, deps.settings, deps.clock())
    return LinkOut(
        url=f"/api/documents/{attachment_id}/download?t={token}",
        expires_in=deps.settings.download_ttl_s,
    )


@router.get("/{attachment_id}/download")
def download(attachment_id: int, request: Request, t: str = "") -> Response:
    deps = get_deps(request)
    if not read_download(t, attachment_id, deps.settings, deps.clock()):
        raise HTTPException(403, "link inválido ou expirado")
    row = deps.index_store.attachment(attachment_id)
    if row is None:
        raise HTTPException(404, "documento não encontrado")
    try:
        data = fetch_attachment(deps.gmail(), row.email_id, row.part_id)
    except LookupError as exc:
        raise HTTPException(410, "o e-mail ou o anexo não existe mais no Gmail") from exc
    deps.log.record("download", request_ip(request), f"anexo #{row.id} {row.filename[:200]}",
                    optional_user(request))
    return Response(
        content=data,
        media_type=row.mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename)}"},
    )
