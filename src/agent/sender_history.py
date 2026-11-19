"""What our own records say about a sender (satisfies `ports.SenderHistorySource`)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import CategoryCount, SenderStats
from src.db.models import Email


class DbSenderHistory:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def stats(self, address: str) -> SenderStats:
        address = address.lower()
        match = func.lower(Email.sender).contains(address, autoescape=True)
        with self.sf() as s:
            total, last_seen = s.execute(
                select(func.count(), func.max(Email.received_at)).where(match)
            ).one()
            categories = s.execute(
                select(Email.category, func.count())
                .where(match, Email.category.is_not(None))
                .group_by(Email.category)
                .order_by(func.count().desc(), Email.category)
            ).all()
        return SenderStats(
            address=address,
            total=total,
            by_category=tuple(
                CategoryCount(category=c, count=n) for c, n in categories if c is not None
            ),
            last_seen=last_seen,
        )
