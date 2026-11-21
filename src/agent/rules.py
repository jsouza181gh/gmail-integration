"""Automation rules: deterministic conditions -> action, no LLM involved."""
from collections.abc import Callable
from datetime import UTC, datetime
from difflib import get_close_matches
from email.utils import parseaddr

from sqlalchemy import ColumnElement, false, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import NEVER_AUTO, ActionKind, RuleMode, RuleRef, as_action_kind
from src.classification.taxonomy import Classification
from src.db.models import AutomationRule, Email
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


class RuleCondition(Schema):
    """AND of the non-null fields; `unless_document` excludes emails with a document."""

    sender_address: str | None = None
    sender_domain: str | None = None
    category: str | None = None
    subject_contains: str | None = None
    unless_document: bool = False

    @classmethod
    def of(cls, rule: AutomationRule) -> "RuleCondition":
        return cls(
            sender_address=rule.sender_address,
            sender_domain=rule.sender_domain,
            category=rule.category,
            subject_contains=rule.subject_contains,
            unless_document=rule.unless_document,
        )

    def has_condition(self) -> bool:
        fields = (self.sender_address, self.sender_domain, self.category, self.subject_contains)
        return any(f is not None and f.strip() for f in fields)

    def matches(
        self,
        sender: str | None,
        subject: str | None,
        category: str | None,
        has_document: bool | None,
    ) -> bool:
        address, domain = _sender_parts(sender)
        checks = [
            self.sender_address is None or self.sender_address.lower() == address,
            self.sender_domain is None or self.sender_domain.lower() == domain,
            self.category is None
            or (category is not None and self.category.casefold() == category.casefold()),
            self.subject_contains is None
            or self.subject_contains.casefold() in (subject or "").casefold(),
            not (self.unless_document and has_document),
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
                matched = RuleCondition.of(r).matches(
                    email.sender, email.subject, classification.category,
                    classification.has_document,
                )
                if action is None or not matched:
                    continue
                out.append(
                    RuleMatch(rule=RuleRef(rule_id=r.id, name=r.name, mode=mode), action=action)
                )
        return tuple(out)

    def existing(self, cond: RuleCondition, limit: int) -> tuple[list[str], int]:
        """Stored emails the condition matches: the newest `limit` ids and the total.

        SQL narrows by substring; the exact check is the same `matches` new
        mail goes through.
        """
        where: list[ColumnElement[bool]] = []
        if cond.sender_address:
            where.append(Email.sender.icontains(cond.sender_address.strip(), autoescape=True))
        if cond.sender_domain:
            where.append(Email.sender.icontains("@" + cond.sender_domain.strip(), autoescape=True))
        if cond.subject_contains:
            where.append(Email.subject.icontains(cond.subject_contains, autoescape=True))
        if cond.category:
            where.append(Email.category.is_not(None))
        if cond.unless_document:
            where.append(or_(Email.has_document.is_(None), Email.has_document == false()))
        q = (
            select(Email.id, Email.sender, Email.subject, Email.category, Email.has_document)
            .where(*where)
            .order_by(Email.received_at.desc().nulls_last(), Email.id.desc())
        )
        ids: list[str] = []
        total = 0
        with self.sf() as s:
            for email_id, sender, subject, category, has_document in s.execute(q):
                if cond.matches(sender, subject, category, has_document):
                    total += 1
                    if len(ids) < limit:
                        ids.append(email_id)
        return ids, total

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
        unless_document: bool = False,
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
                unless_document=unless_document,
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

    def existing(self, cond: RuleCondition, limit: int) -> tuple[list[str], int]:
        return RuleEngine(self.sf).existing(cond, limit)

    def known_senders(self, address: str) -> tuple[int, tuple[str, ...]]:
        """Stored emails from exactly `address`, and up to 3 known addresses
        that look like it (a typo or an invented spelling)."""
        target = address.strip().lower()
        with self.sf() as s:
            senders = list(s.scalars(select(Email.sender).where(Email.sender.is_not(None)).distinct()))
        addresses = [parseaddr(x)[1].lower() for x in senders if x]
        exact = sum(1 for a in addresses if a == target)
        if exact:
            return exact, ()
        close = get_close_matches(target, sorted(set(a for a in addresses if a)), n=3, cutoff=0.8)
        return 0, tuple(close)

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
