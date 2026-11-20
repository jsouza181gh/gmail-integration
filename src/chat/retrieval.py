"""What the chat tools read: search over the index, emails and documents.

`DbRetriever` mixes semantic search (query embedding + pgvector) with a
keyword pass that catches names, numbers and codes embeddings blur. On
SQLite (tests) only the keyword pass runs.
"""
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.ports import MailReader
from src.agent.types import DocHit, MailHit, MailItem
from src.db.models import Attachment, Email
from src.db.rows import EmailRow
from src.gmail.messages import parse_date
from src.indexing.store import IndexStore, SearchHit
from src.llm.errors import LLMError
from src.llm.types import CallContext, EmbedInput, Embedder
from src.utils.console import log

DOC_TEXT_WORDS = 5


def _doc_hit(a: Attachment) -> DocHit:
    return DocHit(
        id=a.id, email_id=a.email_id, doc_class=a.doc_class, doc_type=a.doc_type,
        title=a.title or a.filename, issuer=a.issuer, amount_cents=a.amount_cents,
        due_on=a.due_on, filename=a.filename, mime_type=a.mime_type, size=a.size,
    )


class DbRetriever:
    def __init__(self, index: IndexStore, embedder: Embedder) -> None:
        self.index = index
        self.embedder = embedder

    def _vector(self, query: str, k: int, call: CallContext) -> list[SearchHit]:
        with self.index.sf() as s:
            if s.get_bind().dialect.name != "postgresql":
                return []
        try:
            result = self.embedder.embed(
                [EmbedInput(text=query)], purpose="chat_search", task="RETRIEVAL_QUERY",
                context=call,
            )
        except LLMError as exc:
            # The embedding quota is shared with the backfill and can run out for
            # the day: the keyword pass still answers.
            log(f"busca semântica indisponível, só palavras-chave: {exc}")
            return []
        return self.index.search(result.vectors[0], k)

    def search(self, query: str, k: int, call: CallContext) -> tuple[MailHit, ...]:
        merged: dict[tuple[str, str], SearchHit] = {}
        for hit in [*self._vector(query, k, call), *self.index.keyword(query, max(2, k // 2))]:
            key = (hit.kind, hit.id)
            if key not in merged or hit.score > merged[key].score:
                merged[key] = hit
        hits = list(merged.values())
        email_ids = [h.id for h in hits if h.kind == "email"]
        doc_ids = [int(h.id) for h in hits if h.kind == "attachment"]
        with self.index.sf() as s:
            emails = {e.id: e for e in s.scalars(select(Email).where(Email.id.in_(email_ids)))}
            docs = {
                a.id: a for a in s.scalars(select(Attachment).where(Attachment.id.in_(doc_ids)))
            }
        out: list[MailHit] = []
        for h in hits:
            if h.kind == "email" and (e := emails.get(h.id)) is not None:
                when = e.received_at.date().isoformat() if e.received_at else e.date
                out.append(MailHit(kind="email", id=e.id, date=when, category=e.category,
                                   sender=e.sender, title=e.subject or "(sem assunto)",
                                   score=h.score))
            elif h.kind == "attachment" and (a := docs.get(int(h.id))) is not None:
                kind = "/".join(x for x in (a.doc_class, a.doc_type) if x) or None
                out.append(MailHit(kind="document", id=str(a.id),
                                   date=a.due_on.isoformat() if a.due_on else None,
                                   category=kind, sender=a.issuer, title=a.title or a.filename,
                                   score=h.score))
        return tuple(out)


class DbEmailLookup:
    def __init__(self, session_factory: sessionmaker[Session], mail: MailReader) -> None:
        self.sf = session_factory
        self.mail = mail

    def email(self, email_id: str) -> EmailRow | None:
        with self.sf() as s:
            row = s.get(Email, email_id)
            return None if row is None else EmailRow.model_validate(row)

    def body(self, email_id: str) -> str | None:
        with self.sf() as s:
            row = s.get(Email, email_id)
            return row.body if row is not None and row.body else None

    def ensure(self, email_id: str) -> EmailRow | None:
        found = self.email(email_id)
        if found is not None:
            return found
        item = self.mail.item(email_id)
        return None if item is None else self.remember(item)

    def remember(self, item: MailItem) -> EmailRow:
        with self.sf.begin() as s:
            row = s.get(Email, item.id)
            if row is None:
                row = Email(id=item.id, sender=item.sender, subject=item.subject,
                            date=item.date, snippet=item.snippet,
                            received_at=parse_date(item.date), attempts=0, source="chat")
                s.add(row)
                s.flush()
            return EmailRow.model_validate(row)


class DbDocCatalog:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def find(
        self, doc_class: str | None, doc_type: str | None, text: str | None, limit: int
    ) -> tuple[DocHit, ...]:
        q = select(Attachment).where(Attachment.status == "done")
        if doc_class:
            q = q.where(Attachment.doc_class == doc_class)
        if doc_type:
            q = q.where(Attachment.doc_type == doc_type)
        for word in (text or "").split()[:DOC_TEXT_WORDS]:
            q = q.where(or_(
                Attachment.title.icontains(word, autoescape=True),
                Attachment.issuer.icontains(word, autoescape=True),
                Attachment.filename.icontains(word, autoescape=True),
                Attachment.summary.icontains(word, autoescape=True),
            ))
        q = q.order_by(Attachment.issued_on.desc().nulls_last(), Attachment.id.desc()).limit(limit)
        with self.sf() as s:
            return tuple(_doc_hit(a) for a in s.scalars(q))

    def get(self, ids: Sequence[int]) -> tuple[DocHit, ...]:
        """Analyzed documents among `ids` (others are not shareable)."""
        with self.sf() as s:
            rows = s.scalars(
                select(Attachment).where(Attachment.id.in_(list(ids)), Attachment.status == "done")
            )
            return tuple(_doc_hit(a) for a in rows)
