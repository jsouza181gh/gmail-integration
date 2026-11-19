"""Resolves whether an action runs on its own, waits for approval, or is denied.

Order: a `deny` row wins; otherwise a rule's mode (if any) or the row's
policy (default `suggest`); finally the code invariant `NEVER_AUTO` turns
any `auto` for those kinds into `suggest`, whatever the database says.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import NEVER_AUTO, PolicyDecision, PolicyTarget, RuleMode, Trigger
from src.db.models import ToolPolicy

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
