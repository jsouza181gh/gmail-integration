"""The approval queue: list, approve, reject (with feedback) and lifecycle.

Every verdict leaves a `feedback` row; a comment also becomes a KB entry
(scope `decision`) that the decision prompt reads from then on. Only
pending actions can be decided; approving enqueues the execution job.
"""
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Literal

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import AutomationRule, Email, Feedback, KbEntry, SuggestedAction
from src.db.rows import PendingActionRow
from src.jobs.store import JobStore
from src.jobs.types import ExecuteActionJob

Verdict = Literal["approve", "reject"]
FeedbackSource = Literal["cli", "ui", "chat", "audio"]
ERROR_MAX = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _kb_text(verdict: Verdict, action: SuggestedAction, email: Email, comment: str) -> str:
    """KB entries reach the system prompt, so they carry only what the user
    wrote plus our own vocabulary: never the subject or the agent's reason,
    which derive from third-party email content (prompt injection)."""
    who = parseaddr(email.sender or "")[1] or "remetente sem endereço"
    head = "Recusado" if verdict == "reject" else "Aprovado"
    return f"{head}: {action.kind} em e-mail de {who}. Comentário do usuário: {comment}"


class ApprovalService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        jobs: JobStore,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sf = session_factory
        self.jobs = jobs
        self._now = clock

    def pending(
        self,
        *,
        ids: Sequence[int] = (),
        sender: str | None = None,
        rule_id: int | None = None,
        kind: str | None = None,
    ) -> tuple[PendingActionRow, ...]:
        q: Select[SuggestedAction, Email] = (
            select(SuggestedAction, Email)
            .join(Email, Email.id == SuggestedAction.email_id)
            .where(SuggestedAction.status == "pending")
            .order_by(SuggestedAction.id)
        )
        if ids:
            q = q.where(SuggestedAction.id.in_(list(ids)))
        if sender:
            q = q.where(Email.sender.icontains(sender, autoescape=True))
        if rule_id is not None:
            q = q.where(SuggestedAction.rule_id == rule_id)
        if kind:
            q = q.where(SuggestedAction.kind == kind)
        with self.sf() as s:
            return tuple(
                PendingActionRow(
                    id=a.id,
                    email_id=a.email_id,
                    sender=e.sender,
                    subject=e.subject,
                    kind=a.kind,
                    reason=a.reason,
                    status=a.status,
                    origin=a.origin,
                    rule_id=a.rule_id,
                    created_at=a.created_at,
                )
                for a, e in s.execute(q)
            )

    def _decide(
        self,
        ids: Sequence[int],
        verdict: Verdict,
        comment: str | None,
        source: FeedbackSource,
    ) -> list[int]:
        now = self._now()
        batch = uuid.uuid4().hex
        decided: list[int] = []
        comment = comment.strip() if comment else None
        with self.sf.begin() as s:
            rows = s.execute(
                select(SuggestedAction, Email)
                .join(Email, Email.id == SuggestedAction.email_id)
                .where(SuggestedAction.id.in_(list(ids)), SuggestedAction.status == "pending")
                .order_by(SuggestedAction.id)
            )
            for action, email in rows:
                action.status = "approved" if verdict == "approve" else "rejected"
                action.decided_at = now
                action.batch_id = batch
                if action.rule_id is not None:
                    counter = (
                        AutomationRule.approved_count
                        if verdict == "approve"
                        else AutomationRule.rejected_count
                    )
                    s.execute(
                        update(AutomationRule)
                        .where(AutomationRule.id == action.rule_id)
                        .values({counter: counter + 1})
                    )
                kb_id: int | None = None
                if comment:
                    kb = KbEntry(
                        kind="feedback",
                        scope="decision",
                        text=_kb_text(verdict, action, email, comment),
                        source="approval",
                        active=True,
                        created_at=now,
                    )
                    s.add(kb)
                    s.flush()
                    kb_id = kb.id
                s.add(
                    Feedback(
                        action_id=action.id,
                        email_id=email.id,
                        verdict=verdict,
                        text=comment,
                        source=source,
                        kb_entry_id=kb_id,
                        created_at=now,
                    )
                )
                decided.append(action.id)
        return decided

    def approve(
        self, ids: Sequence[int], comment: str | None = None, source: FeedbackSource = "cli"
    ) -> int:
        decided = self._decide(ids, "approve", comment, source)
        for action_id in decided:
            self.jobs.enqueue(ExecuteActionJob(action_id=action_id))
        return len(decided)

    def reject(
        self, ids: Sequence[int], comment: str | None = None, source: FeedbackSource = "cli"
    ) -> int:
        return len(self._decide(ids, "reject", comment, source))

    def approved(self, action_id: int) -> tuple[str, str] | None:
        """(kind, email_id) when the action is approved and not yet executed."""
        with self.sf() as s:
            a = s.get(SuggestedAction, action_id)
            if a is None or a.status != "approved":
                return None
            return a.kind, a.email_id

    def mark_executed(self, action_id: int) -> None:
        with self.sf.begin() as s:
            a = s.get_one(SuggestedAction, action_id)
            a.status = "executed"
            a.executed_at = self._now()
            a.error = None

    def mark_failed(self, action_id: int, error: str) -> None:
        with self.sf.begin() as s:
            a = s.get_one(SuggestedAction, action_id)
            if a.status == "approved":
                a.status = "failed"
                a.error = error[:ERROR_MAX]
