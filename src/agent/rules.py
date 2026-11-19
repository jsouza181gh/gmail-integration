"""Automation rules: deterministic conditions -> action, no LLM involved."""
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parseaddr

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import NEVER_AUTO, ActionKind, RuleMode, RuleRef, as_action_kind
from src.classification.taxonomy import Classification
from src.db.models import AutomationRule
from src.db.rows import EmailRow, RuleRow
from src.llm.types import Schema


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


class RuleAdmin:
    """Create and manage rules. New rules always start in `suggest` mode."""

    def __init__(
        self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.sf = session_factory
        self._now = clock

    def add(
        self,
        *,
        name: str,
        action: str,
        sender_address: str | None = None,
        sender_domain: str | None = None,
        category: str | None = None,
        subject_contains: str | None = None,
        source: str = "cli",
    ) -> int:
        kind = as_action_kind(action)
        if kind is None:
            raise ValueError(f"ação desconhecida: {action}")
        conditions = (sender_address, sender_domain, category, subject_contains)
        if all(c is None or not c.strip() for c in conditions):
            raise ValueError("a regra precisa de pelo menos uma condição")
        with self.sf.begin() as s:
            rule = AutomationRule(
                name=name.strip(),
                sender_address=sender_address.strip().lower() if sender_address else None,
                sender_domain=sender_domain.strip().lower() if sender_domain else None,
                category=category.strip() if category else None,
                subject_contains=subject_contains.strip() if subject_contains else None,
                action=kind,
                mode="suggest",
                active=True,
                fired_count=0,
                approved_count=0,
                rejected_count=0,
                source=source,
                created_at=self._now(),
            )
            s.add(rule)
            s.flush()
            return rule.id

    def list(self) -> tuple[RuleRow, ...]:
        with self.sf() as s:
            rows = s.scalars(select(AutomationRule).order_by(AutomationRule.id))
            return tuple(RuleRow.model_validate(r) for r in rows)

    def get(self, rule_id: int) -> RuleRow | None:
        with self.sf() as s:
            row = s.get(AutomationRule, rule_id)
            return RuleRow.model_validate(row) if row else None

    def promote(self, rule_id: int) -> None:
        with self.sf.begin() as s:
            rule = s.get(AutomationRule, rule_id)
            if rule is None:
                raise ValueError(f"regra #{rule_id} não existe")
            kind = as_action_kind(rule.action)
            if kind is None or kind in NEVER_AUTO:
                raise ValueError(f"'{rule.action}' nunca pode rodar sozinha (fica em suggest)")
            rule.mode = "auto"

    def disable(self, rule_id: int) -> None:
        with self.sf.begin() as s:
            rule = s.get(AutomationRule, rule_id)
            if rule is None:
                raise ValueError(f"regra #{rule_id} não existe")
            rule.active = False
