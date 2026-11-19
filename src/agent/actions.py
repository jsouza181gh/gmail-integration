"""Turns a proposed action into a suggestion, an auto-approved job, or nothing.

Satisfies `ports.ActionSink`. One live proposal per (email, kind): a second
one reports `duplicate` (the partial unique index backs this up).
"""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.agent.ports import PolicyReader
from src.agent.types import ActionKind, ProposalRef, RuleRef, Trigger
from src.db.models import SuggestedAction
from src.jobs.store import JobStore
from src.jobs.types import ExecuteActionJob

LIVE = ("pending", "approved")
REASON_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ActionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        jobs: JobStore,
        policy: PolicyReader,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sf = session_factory
        self.jobs = jobs
        self.policy = policy
        self._now = clock

    def _live(self, s: Session, email_id: str, kind: ActionKind) -> int | None:
        return s.scalar(
            select(SuggestedAction.id).where(
                SuggestedAction.email_id == email_id,
                SuggestedAction.kind == kind,
                SuggestedAction.status.in_(LIVE),
            )
        )

    def propose(
        self,
        email_id: str,
        kind: ActionKind,
        reason: str,
        *,
        run_id: int | None,
        trigger: Trigger,
        rule: RuleRef | None = None,
    ) -> ProposalRef:
        decision = self.policy.resolve(kind, trigger, rule_mode=rule.mode if rule else None)
        if decision == "deny":
            return ProposalRef(action_id=None, kind=kind, email_id=email_id, outcome="denied")

        now = self._now()
        auto = decision == "auto"
        try:
            with self.sf.begin() as s:
                existing = self._live(s, email_id, kind)
                if existing is not None:
                    return ProposalRef(
                        action_id=existing, kind=kind, email_id=email_id, outcome="duplicate"
                    )
                row = SuggestedAction(
                    email_id=email_id,
                    kind=kind,
                    reason=reason.strip()[:REASON_MAX],
                    status="approved" if auto else "pending",
                    origin="rule" if rule else "agent",
                    run_id=run_id,
                    rule_id=rule.rule_id if rule else None,
                    created_at=now,
                    decided_at=now if auto else None,
                )
                s.add(row)
                s.flush()
                action_id = row.id
        except IntegrityError:  # lost a race with a concurrent proposal
            with self.sf() as s:
                existing = self._live(s, email_id, kind)
            return ProposalRef(action_id=existing, kind=kind, email_id=email_id, outcome="duplicate")

        if auto:
            self.jobs.enqueue(ExecuteActionJob(action_id=action_id))
        return ProposalRef(
            action_id=action_id,
            kind=kind,
            email_id=email_id,
            outcome="auto" if auto else "suggested",
        )
