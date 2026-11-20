"""Knowledge base reads (satisfies `ports.KbReader`).

No embeddings yet: every active entry of the requested scopes is returned
(the KB is small; similarity search arrives with pgvector in subproject 3).
"""
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import Channel, audit
from src.db.models import KbEntry
from src.llm.types import Schema

KbScope = Literal["classification", "decision"]
KB_TEXT_MAX = 1000


class DbKb:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def entries(self, scopes: Sequence[str]) -> tuple[str, ...]:
        with self.sf() as s:
            rows = s.scalars(
                select(KbEntry.text)
                .where(KbEntry.active, KbEntry.scope.in_(list(scopes)))
                .order_by(KbEntry.id)
            )
            return tuple(rows)


class KbRow(Schema):
    id: int
    kind: str
    scope: str
    text: str
    source: str
    active: bool
    created_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KbAdmin:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def list(self, scope: str | None = None) -> list[KbRow]:
        q = select(KbEntry).order_by(KbEntry.id.desc())
        if scope:
            q = q.where(KbEntry.scope == scope)
        with self.sf() as s:
            return [
                KbRow(id=e.id, kind=e.kind, scope=e.scope, text=e.text, source=e.source,
                      active=e.active, created_at=e.created_at)
                for e in s.scalars(q)
            ]

    def add(
        self, scope: KbScope, text: str, channel: Channel = "ui", source: str | None = None
    ) -> KbRow:
        """`source` is the entry's origin (e.g. "audio"); defaults to the channel."""
        text = text.strip()[:KB_TEXT_MAX]
        if not text:
            raise ValueError("texto vazio")
        now = self._now()
        with self.sf.begin() as s:
            e = KbEntry(kind="preference", scope=scope, text=text, source=source or channel,
                        active=True, created_at=now)
            s.add(e)
            s.flush()
            audit(s, "kb_entry", e.id, channel, None, text, now)
            return KbRow(id=e.id, kind=e.kind, scope=e.scope, text=e.text, source=e.source,
                         active=e.active, created_at=now)

    def deactivate(self, entry_id: int, channel: Channel = "ui") -> bool:
        now = self._now()
        with self.sf.begin() as s:
            e = s.get(KbEntry, entry_id)
            if e is None or not e.active:
                return False
            e.active = False
            audit(s, "kb_entry", entry_id, channel, "ativa", "inativa", now)
            return True
