"""Persistence of the index: bodies, email vectors, attachment rows.

Every method is its own transaction and idempotent, so a job interrupted at
any point can simply run again.
"""
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Attachment, Email, EmailEmbedding, RunEmail
from src.db.rows import AttachmentRow
from src.gmail.full import AttachmentRef, FullMessage
from src.llm.types import Schema


class EmailText(Schema):
    """What gets embedded for one email."""

    id: str
    sender: str | None
    subject: str | None
    date: str | None
    body: str


class AnalysisRecord(Schema):
    doc_class: str
    doc_type: str
    title: str
    summary: str
    issuer: str | None
    amount_cents: int | None
    issued_on: date | None
    due_on: date | None
    barcode: str | None
    text: str | None
    embedding: tuple[float, ...]
    embedding_model: str


class EmailHeader(Schema):
    sender: str | None
    subject: str | None


class NewAttachment(Schema):
    ref: AttachmentRef
    skip_reason: str | None  # None: to be analyzed


def _now() -> datetime:
    return datetime.now(UTC)


class IndexStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    # --- emails -------------------------------------------------------------

    def needs_index(self, ids: Sequence[str]) -> list[str]:
        """Ids not yet fetched in full (unknown ones included), in order."""
        with self.sf() as s:
            done = set(
                s.scalars(
                    select(Email.id).where(
                        Email.id.in_(ids),
                        (Email.indexed_at.is_not(None)) | (Email.index_error.is_not(None)),
                    )
                )
            )
        return [i for i in dict.fromkeys(ids) if i not in done]

    def save_indexed(
        self, full: FullMessage, attachments: Sequence[NewAttachment], run_id: int | None
    ) -> list[int]:
        """Store body + attachment rows (and the email if new); link to the run.

        Returns the ids of attachments waiting for analysis.
        """
        sm = full.summary
        with self.sf.begin() as s:
            email = s.get(Email, sm.id)
            if email is None:
                email = Email(
                    id=sm.id, sender=sm.sender, subject=sm.subject, date=sm.date,
                    snippet=sm.snippet, received_at=sm.received_at, attempts=0,
                )
                s.add(email)
            email.body = full.body
            email.indexed_at = _now()
            email.index_error = None
            s.flush()
            if run_id is not None and s.get(RunEmail, (run_id, sm.id)) is None:
                s.add(RunEmail(run_id=run_id, email_id=sm.id))
            existing = {
                a.part_id: a for a in s.scalars(select(Attachment).where(Attachment.email_id == sm.id))
            }
            for new in attachments:
                if new.ref.part_id in existing:
                    continue
                row = Attachment(
                    email_id=sm.id,
                    part_id=new.ref.part_id,
                    filename=new.ref.filename[:500],
                    mime_type=new.ref.mime_type[:128],
                    size=new.ref.size,
                    status="pending" if new.skip_reason is None else "skipped",
                    skip_reason=new.skip_reason,
                    doc_class=None if new.skip_reason is None else "outro",
                )
                s.add(row)
                existing[row.part_id] = row
            s.flush()
            return [a.id for a in existing.values() if a.status == "pending"]

    def mark_gone(self, email_id: str) -> bool:
        """The message no longer exists in Gmail; False if we never had it."""
        with self.sf.begin() as s:
            email = s.get(Email, email_id)
            if email is None:
                return False
            email.index_error = "gone"
            email.indexed_at = _now()
            return True

    def needs_embedding(self, ids: Sequence[str]) -> list[EmailText]:
        with self.sf() as s:
            rows = s.scalars(
                select(Email)
                .outerjoin(EmailEmbedding, EmailEmbedding.email_id == Email.id)
                .where(
                    Email.id.in_(ids),
                    Email.indexed_at.is_not(None),
                    Email.index_error.is_(None),
                    EmailEmbedding.email_id.is_(None),
                )
                .order_by(Email.id)
            )
            return [
                EmailText(id=e.id, sender=e.sender, subject=e.subject, date=e.date, body=e.body or "")
                for e in rows
            ]

    def save_email_embeddings(
        self, model: str, vectors: Mapping[str, tuple[float, ...]]
    ) -> None:
        with self.sf.begin() as s:
            for email_id, vec in vectors.items():
                row = s.get(EmailEmbedding, email_id)
                if row is None:
                    s.add(EmailEmbedding(email_id=email_id, model=model, embedding=vec))
                else:
                    row.model, row.embedding = model, vec

    # --- attachments --------------------------------------------------------

    def attachment(self, attachment_id: int) -> AttachmentRow | None:
        with self.sf() as s:
            row = s.get(Attachment, attachment_id)
            return None if row is None else AttachmentRow.model_validate(row)

    def email_header(self, email_id: str) -> EmailHeader:
        with self.sf() as s:
            e = s.get(Email, email_id)
            return EmailHeader(sender=e.sender if e else None, subject=e.subject if e else None)

    def skip_attachment(self, attachment_id: int, reason: str) -> None:
        with self.sf.begin() as s:
            a = s.get_one(Attachment, attachment_id)
            a.status, a.skip_reason, a.doc_class = "skipped", reason, "outro"
            a.analyzed_at = _now()

    def fail_attachment(self, attachment_id: int, error: str) -> None:
        with self.sf.begin() as s:
            a = s.get_one(Attachment, attachment_id)
            a.status, a.error, a.analyzed_at = "failed", error[:2000], _now()

    def save_analysis(self, attachment_id: int, rec: AnalysisRecord) -> None:
        with self.sf.begin() as s:
            a = s.get_one(Attachment, attachment_id)
            for field, value in rec.model_dump().items():
                setattr(a, field, value)
            a.embedding = rec.embedding
            a.status, a.error, a.analyzed_at = "done", None, _now()
