"""Resolves whether an action runs on its own, waits for approval, or is denied.

Order: a `deny` row wins; otherwise a rule's mode (if any) or the row's
policy (default `suggest`); finally the code invariant `NEVER_AUTO` turns
any `auto` for those kinds into `suggest`, whatever the database says.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import Channel, audit
from src.agent.types import NEVER_AUTO, PolicyDecision, PolicyTarget, RuleMode, Trigger
from src.db.models import ToolPolicy
from src.llm.types import Schema

_DECISIONS: dict[str, PolicyDecision] = {"auto": "auto", "suggest": "suggest", "deny": "deny"}


class PolicyResolver:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def resolve(
        self,
        target: PolicyTarget,
        trigger: Trigger,
        *,
        rule_mode: RuleMode | None = None,
    ) -> PolicyDecision:
        with self.sf() as s:
            stored = s.scalar(
                select(ToolPolicy.policy).where(
                    ToolPolicy.target == target, ToolPolicy.trigger == trigger
                )
            )
        # An unknown stored value is treated as the safe default.
        db = _DECISIONS.get(stored or "suggest", "suggest")
        if db == "deny":
            return "deny"
        decision: PolicyDecision = rule_mode if rule_mode is not None else db
        if decision == "auto" and target in NEVER_AUTO:
            return "suggest"
        return decision


class PolicyRow(Schema):
    target: str
    trigger: str
    policy: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PolicyAdmin:
    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def list(self) -> list[PolicyRow]:
        with self.sf() as s:
            rows = s.scalars(select(ToolPolicy).order_by(ToolPolicy.target, ToolPolicy.trigger))
            return [PolicyRow(target=r.target, trigger=r.trigger, policy=r.policy) for r in rows]

    def set(
        self, target: PolicyTarget, trigger: Trigger, policy: PolicyDecision, channel: Channel = "ui"
    ) -> None:
        if policy == "auto" and target in NEVER_AUTO:
            raise ValueError(f"'{target}' nunca pode ficar em auto")
        now = self._now()
        with self.sf.begin() as s:
            row = s.scalars(
                select(ToolPolicy).where(ToolPolicy.target == target, ToolPolicy.trigger == trigger)
            ).first()
            before = row.policy if row else None
            if row is None:
                s.add(ToolPolicy(target=target, trigger=trigger, policy=policy))
            else:
                row.policy = policy
            audit(s, "tool_policy", f"{target}/{trigger}", channel, before, policy, now)
