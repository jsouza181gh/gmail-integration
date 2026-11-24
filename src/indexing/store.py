"""Persistence of the index: bodies, email vectors, attachment rows.

Every method is its own transaction and idempotent, so a job interrupted at
any point can simply run again.
"""
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import ColumnElement, Float, cast, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.scope import email_key
from src.db.models import Attachment, Email, EmailEmbedding, Run, RunEmail
from src.db.rows import AttachmentRow, EmailRow, RunRow
from src.db.vector import Vector
from src.gmail.full import AttachmentRef, FullMessage
from src.llm.types import Schema

KEYWORD_WORDS = 5


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


class SearchHit(Schema):
    kind: Literal["email", "attachment"]
    id: str  # email id, or attachment id as text
    title: str
    score: float  # cosine similarity, higher is closer


class RunCounts(Schema):
    emails: int
    classified: int
    indexed: int
    embedded: int
    attachments: dict[str, int]


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
            email = s.get(Email, email_key(sm.id))
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
            email = s.get(Email, email_key(email_id))
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
                row = s.get(EmailEmbedding, email_key(email_id))
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
            e = s.get(Email, email_key(email_id))
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

    # --- backfill -------------------------------------------------------------

    def backfill_run(self, query: str) -> RunRow:
        """The one run of this backfill query; created on first use only."""
        with self.sf.begin() as s:
            run = s.scalars(select(Run).where(Run.query == query).order_by(Run.id)).first()
            if run is None:
                run = Run(query=query)
                s.add(run)
                s.flush()
                s.refresh(run)
            return RunRow.model_validate(run)

    def unclassified(self, ids: Sequence[str]) -> list[EmailRow]:
        with self.sf() as s:
            rows = s.scalars(
                select(Email).where(Email.id.in_(ids), Email.category.is_(None)).order_by(Email.id)
            )
            return [EmailRow.model_validate(r) for r in rows]

    def bodies(self, ids: Sequence[str]) -> dict[str, str]:
        with self.sf() as s:
            return {
                i: b or ""
                for i, b in s.execute(select(Email.id, Email.body).where(Email.id.in_(ids)))
            }

    def run_counts(self, run_id: int) -> RunCounts:
        in_run = select(RunEmail.email_id).where(RunEmail.run_id == run_id).scalar_subquery()
        with self.sf() as s:
            def count(*where: ColumnElement[bool]) -> int:
                q = select(func.count()).select_from(Email).where(Email.id.in_(in_run))
                for w in where:
                    q = q.where(w)
                return s.scalar(q) or 0

            embedded = s.scalar(
                select(func.count())
                .select_from(EmailEmbedding)
                .where(EmailEmbedding.email_id.in_(in_run))
            ) or 0
            attachments = {
                status: n
                for status, n in s.execute(
                    select(Attachment.status, func.count())
                    .where(Attachment.email_id.in_(in_run))
                    .group_by(Attachment.status)
                )
            }
            return RunCounts(
                emails=count(),
                classified=count(Email.category.is_not(None)),
                indexed=count(Email.indexed_at.is_not(None)),
                embedded=embedded,
                attachments=attachments,
            )

    # --- search and documents ---------------------------------------------

    def search(self, vector: tuple[float, ...], k: int) -> list[SearchHit]:
        """Nearest emails and documents by cosine distance (Postgres only)."""
        with self.sf() as s:
            if s.get_bind().dialect.name != "postgresql":
                raise NotImplementedError("busca vetorial exige Postgres + pgvector")
            q = cast(vector, Vector())
            e_dist = EmailEmbedding.embedding.op("<=>", return_type=Float)(q)
            emails = s.execute(
                select(Email.id, Email.subject, Email.sender, e_dist)
                .join(EmailEmbedding, EmailEmbedding.email_id == Email.id)
                .order_by(e_dist)
                .limit(k)
            ).all()
            a_dist = Attachment.embedding.op("<=>", return_type=Float)(q)
            docs = s.execute(
                select(Attachment.id, Attachment.title, Attachment.filename, a_dist)
                .where(Attachment.embedding.is_not(None))
                .order_by(a_dist)
                .limit(k)
            ).all()
        hits = [
            SearchHit(kind="email", id=i, title=f"{subj or '(sem assunto)'} — {sender or ''}",
                      score=1 - float(d))
            for i, subj, sender, d in emails
        ] + [
            SearchHit(kind="attachment", id=str(i), title=title or name, score=1 - float(d))
            for i, title, name, d in docs
        ]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]

    def keyword(self, text: str, k: int) -> list[SearchHit]:
        """Emails and documents containing every word (≥ 3 letters) of `text`.

        The complement of `search` for names, numbers and codes that
        embeddings blur; works on SQLite too. Score is 0 (no similarity).
        """
        words = [w for w in text.split() if len(w) >= 3][:KEYWORD_WORDS]
        if not words:
            return []
        email_where = [
            or_(*(c.icontains(w, autoescape=True) for c in (Email.subject, Email.sender,
                                                             Email.snippet)))
            for w in words
        ]
        doc_where = [
            or_(*(c.icontains(w, autoescape=True) for c in (Attachment.title, Attachment.issuer,
                                                             Attachment.filename)))
            for w in words
        ]
        with self.sf() as s:
            emails = s.execute(
                select(Email.id, Email.subject, Email.sender)
                .where(*email_where)
                .order_by(Email.received_at.desc().nulls_last(), Email.id.desc())
                .limit(k)
            ).all()
            docs = s.execute(
                select(Attachment.id, Attachment.title, Attachment.filename)
                .where(Attachment.status == "done", *doc_where)
                .order_by(Attachment.id.desc())
                .limit(k)
            ).all()
        return [
            SearchHit(kind="email", id=i, title=f"{subj or '(sem assunto)'} — {sender or ''}",
                      score=0.0)
            for i, subj, sender in emails
        ] + [
            SearchHit(kind="attachment", id=str(i), title=title or name, score=0.0)
            for i, title, name in docs
        ]

    def documents(
        self, doc_class: str | None, doc_type: str | None, limit: int
    ) -> list[AttachmentRow]:
        q = select(Attachment).where(Attachment.status == "done")
        if doc_class is not None:
            q = q.where(Attachment.doc_class == doc_class)
        if doc_type is not None:
            q = q.where(Attachment.doc_type == doc_type)
        with self.sf() as s:
            rows = s.scalars(q.order_by(Attachment.id.desc()).limit(limit))
            return [AttachmentRow.model_validate(r) for r in rows]
