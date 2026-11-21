"""Read models of the panel API and the queries behind them.

Typed Pydantic outputs, built straight from SQL; nothing here writes.
"""
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from src.db.models import AgentRun, Attachment, Email, SuggestedAction
from src.llm.types import Schema

IMPORTANT_CLASSES = ("pessoal", "trabalho", "fiscal")
DUE_SOON = timedelta(days=3)


class EmailListItem(Schema):
    id: str
    sender: str | None
    subject: str | None
    snippet: str | None
    received_at: datetime | None
    category: str | None
    is_relevant: bool | None
    deadline: date | None
    has_document: bool | None
    ephemeral: bool | None
    pending_actions: int
    attachments: int
    due_soon: bool = False


class EmailPage(Schema):
    items: list[EmailListItem]
    next_before: str | None  # opaque cursor: "<received_at iso>|<id>"


class DocumentOut(Schema):
    id: int
    email_id: str
    filename: str
    mime_type: str
    size: int
    status: str
    skip_reason: str | None
    doc_class: str | None
    doc_type: str | None
    title: str | None
    summary: str | None
    issuer: str | None
    amount_cents: int | None
    issued_on: date | None
    due_on: date | None
    barcode: str | None
    email_subject: str | None = None
    email_received_at: datetime | None = None


class ActionOut(Schema):
    id: int
    email_id: str
    sender: str | None
    subject: str | None
    kind: str
    reason: str
    status: str
    origin: str
    rule_id: int | None
    run_id: int | None
    created_at: datetime
    has_important_attachment: bool


class RunOut(Schema):
    id: int
    trigger: str
    email_id: str | None
    config_id: int
    status: str
    summary: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class EmailDetail(Schema):
    item: EmailListItem
    reason: str | None
    body: str | None
    attachments: list[DocumentOut]
    actions: list[ActionOut]
    runs: list[RunOut]


def _counts(s: Session, ids: Sequence[str]) -> tuple[dict[str, int], dict[str, int]]:
    pending = {
        e: n for e, n in s.execute(
            select(SuggestedAction.email_id, func.count())
            .where(SuggestedAction.email_id.in_(ids), SuggestedAction.status == "pending")
            .group_by(SuggestedAction.email_id)
        )
    }
    atts = {
        e: n for e, n in s.execute(
            select(Attachment.email_id, func.count())
            .where(Attachment.email_id.in_(ids), Attachment.status != "skipped")
            .group_by(Attachment.email_id)
        )
    }
    return pending, atts


def _items(s: Session, rows: Sequence[Email], today: date) -> list[EmailListItem]:
    pending, atts = _counts(s, [e.id for e in rows])
    return [
        EmailListItem(
            id=e.id, sender=e.sender, subject=e.subject, snippet=e.snippet,
            received_at=e.received_at, category=e.category, is_relevant=e.is_relevant,
            deadline=e.deadline, has_document=e.has_document, ephemeral=e.ephemeral,
            pending_actions=pending.get(e.id, 0), attachments=atts.get(e.id, 0),
            due_soon=e.deadline is not None and e.deadline - today <= DUE_SOON,
        )
        for e in rows
    ]


def _cursor(e: Email) -> str:
    return f"{e.received_at.isoformat() if e.received_at else ''}|{e.id}"


def list_emails(
    s: Session,
    *,
    category: str | None,
    relevant: bool | None,
    q: str | None,
    before: str | None,
    limit: int,
    today: date,
) -> EmailPage:
    stmt: Select[Email] = select(Email).where(Email.source == "inbox")
    if category is not None:
        stmt = stmt.where(Email.category == category)
    if relevant is not None:
        stmt = stmt.where(Email.is_relevant.is_(relevant))
    if q:
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Email.subject.ilike(like), Email.sender.ilike(like), Email.snippet.ilike(like)))
    if before:
        at, _, last_id = before.partition("|")
        if at:
            ts = datetime.fromisoformat(at)
            stmt = stmt.where(or_(Email.received_at < ts, and_(Email.received_at == ts, Email.id > last_id)))
    stmt = stmt.order_by(Email.received_at.desc().nulls_last(), Email.id).limit(limit + 1)
    rows = list(s.scalars(stmt))
    more = len(rows) > limit
    rows = rows[:limit]
    return EmailPage(
        items=_items(s, rows, today),
        next_before=_cursor(rows[-1]) if more and rows else None,
    )


def relevant(s: Session, today: date, limit: int = 200) -> list[EmailListItem]:
    rows = list(s.scalars(
        select(Email).where(Email.is_relevant.is_(True), Email.source == "inbox")
        .order_by(Email.deadline.asc().nulls_last(), Email.received_at.desc().nulls_last())
        .limit(limit)
    ))
    return _items(s, rows, today)


def document_out(a: Attachment, email: Email | None = None) -> DocumentOut:
    return DocumentOut(
        id=a.id, email_id=a.email_id, filename=a.filename, mime_type=a.mime_type, size=a.size,
        status=a.status, skip_reason=a.skip_reason, doc_class=a.doc_class, doc_type=a.doc_type,
        title=a.title, summary=a.summary, issuer=a.issuer, amount_cents=a.amount_cents,
        issued_on=a.issued_on, due_on=a.due_on, barcode=a.barcode,
        email_subject=email.subject if email else None,
        email_received_at=email.received_at if email else None,
    )


def run_out(r: AgentRun) -> RunOut:
    return RunOut(
        id=r.id, trigger=r.trigger, email_id=r.email_id, config_id=r.config_id, status=r.status,
        summary=r.summary, error=r.error, started_at=r.started_at, finished_at=r.finished_at,
    )


def actions(
    s: Session,
    *,
    status: str = "pending",
    kind: str | None = None,
    sender: str | None = None,
    rule_id: int | None = None,
    email_id: str | None = None,
    limit: int = 500,
) -> list[ActionOut]:
    stmt = (
        select(SuggestedAction, Email)
        .join(Email, Email.id == SuggestedAction.email_id)
        .where(SuggestedAction.status == status)
    )
    if kind:
        stmt = stmt.where(SuggestedAction.kind == kind)
    if sender:
        stmt = stmt.where(Email.sender.ilike(f"%{sender[:200]}%"))
    if rule_id is not None:
        stmt = stmt.where(SuggestedAction.rule_id == rule_id)
    if email_id is not None:
        stmt = stmt.where(SuggestedAction.email_id == email_id)
    rows = s.execute(stmt.order_by(SuggestedAction.created_at.desc()).limit(limit)).all()
    important = set(s.scalars(
        select(Attachment.email_id).where(
            Attachment.email_id.in_([e.id for _, e in rows]),
            Attachment.doc_class.in_(IMPORTANT_CLASSES),
        )
    ))
    return [
        ActionOut(
            id=a.id, email_id=a.email_id, sender=e.sender, subject=e.subject, kind=a.kind,
            reason=a.reason, status=a.status, origin=a.origin, rule_id=a.rule_id, run_id=a.run_id,
            created_at=a.created_at, has_important_attachment=a.email_id in important,
        )
        for a, e in rows
    ]


def email_detail(s: Session, email_id: str, today: date) -> EmailDetail | None:
    e = s.get(Email, email_id)
    if e is None:
        return None
    atts = s.scalars(select(Attachment).where(Attachment.email_id == email_id).order_by(Attachment.id))
    runs = s.scalars(select(AgentRun).where(AgentRun.email_id == email_id).order_by(AgentRun.id.desc()))
    acts = [a for st in ("pending", "approved", "executed", "rejected", "failed")
            for a in actions(s, status=st, email_id=email_id)]
    return EmailDetail(
        item=_items(s, [e], today)[0],
        reason=e.reason,
        body=e.body,
        attachments=[document_out(a, e) for a in atts],
        actions=acts,
        runs=[run_out(r) for r in runs],
    )
