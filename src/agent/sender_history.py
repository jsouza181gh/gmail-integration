"""What our own records say about a sender (satisfies `ports.SenderHistorySource`)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import ActionOutcomeCount, CategoryCount, SenderStats, as_action_kind
from src.db.models import Email, SuggestedAction


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
            outcomes = s.execute(
                select(SuggestedAction.kind, SuggestedAction.status, func.count())
                .join(Email, Email.id == SuggestedAction.email_id)
                .where(match)
                .group_by(SuggestedAction.kind, SuggestedAction.status)
                .order_by(SuggestedAction.kind, SuggestedAction.status)
            ).all()
        actions = []
        for kind, status, n in outcomes:
            typed = as_action_kind(kind)
            if typed is not None:
                actions.append(ActionOutcomeCount(kind=typed, status=status, count=n))
        return SenderStats(
            address=address,
            total=total,
            by_category=tuple(
                CategoryCount(category=c, count=n) for c, n in categories if c is not None
            ),
            actions=tuple(actions),
            last_seen=last_seen,
        )
