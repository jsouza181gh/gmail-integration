"""Knowledge base reads (satisfies `ports.KbReader`).

No embeddings yet: every active entry of the requested scopes is returned
(the KB is small; similarity search arrives with pgvector in subproject 3).
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import KbEntry


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
