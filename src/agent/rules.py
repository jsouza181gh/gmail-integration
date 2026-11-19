"""Automation rules: deterministic conditions -> action, no LLM involved."""
from email.utils import parseaddr

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import ActionKind, RuleMode, RuleRef, as_action_kind
from src.classification.taxonomy import Classification
from src.db.models import AutomationRule
from src.db.rows import EmailRow
from src.llm.types import Schema


class RuleMatch(Schema):
    rule: RuleRef
    action: ActionKind


def _sender_parts(sender: str | None) -> tuple[str, str]:
    address = parseaddr(sender or "")[1].lower()
    return address, address.rpartition("@")[2]


def _matches(rule: AutomationRule, email: EmailRow, c: Classification) -> bool:
    address, domain = _sender_parts(email.sender)
    checks = [
        rule.sender_address is None or rule.sender_address.lower() == address,
        rule.sender_domain is None or rule.sender_domain.lower() == domain,
        rule.category is None or rule.category.casefold() == c.category.casefold(),
        rule.subject_contains is None
        or rule.subject_contains.casefold() in (email.subject or "").casefold(),
    ]
    return all(checks)


class RuleEngine:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def match(self, email: EmailRow, classification: Classification) -> tuple[RuleMatch, ...]:
        with self.sf() as s:
            rules = s.scalars(
                select(AutomationRule).where(AutomationRule.active).order_by(AutomationRule.id)
            ).all()
            out: list[RuleMatch] = []
            for r in rules:
                action = as_action_kind(r.action)
                mode: RuleMode = "auto" if r.mode == "auto" else "suggest"
                if action is None or not _matches(r, email, classification):
                    continue
                out.append(
                    RuleMatch(rule=RuleRef(rule_id=r.id, name=r.name, mode=mode), action=action)
                )
        return tuple(out)

    def record_fired(self, rule_id: int) -> None:
        with self.sf.begin() as s:
            s.execute(
                update(AutomationRule)
                .where(AutomationRule.id == rule_id)
                .values(fired_count=AutomationRule.fired_count + 1)
            )
