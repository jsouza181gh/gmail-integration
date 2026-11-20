"""Confirmation of chat cards: the only place a chat changes rules, KB,
policies or approvals — always from the user's click, never from the model.

A card is single-shot: the first decision flips it out of `proposed` in its
own transaction, then the change is applied; a failure to apply leaves the
card `failed` with the reason shown to the user.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.kb import KbAdmin
from src.agent.policy import PolicyAdmin
from src.agent.rules import RuleAdmin, RuleCondition, RuleEngine
from src.agent.types import (
    ActionKind,
    ChatChannel,
    KbScopeName,
    PolicyDecision,
    PolicyTarget,
    RuleRef,
    Trigger,
    Verdict,
    as_action_kind,
)
from src.chat.store import CardConflict, CardView, ChatStore
from src.db.models import ChatCard, ChatCardItem

APPLY_EXISTING_MAX = 1000
ERROR_MAX = 500

_TARGETS: dict[str, PolicyTarget] = {
    t: t for t in ("mark_read", "star", "archive", "trash", "block_sender", "apply_labels")
}
_TRIGGERS: dict[str, Trigger] = {"webhook": "webhook", "chat": "chat"}
_DECISIONS: dict[str, PolicyDecision] = {"auto": "auto", "suggest": "suggest", "deny": "deny"}
_VERDICTS: dict[str, Verdict] = {"approve": "approve", "reject": "reject"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Applied(Exception):
    """Carries the reason a confirmed card could not be applied."""


class CardService:
    def __init__(
        self,
        *,
        store: ChatStore,
        rules: RuleAdmin,
        matcher: RuleEngine,
        kb: KbAdmin,
        policies: PolicyAdmin,
        approvals: ApprovalService,
        actions: ActionService,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.sf: sessionmaker[Session] = store.sf
        self.rules = rules
        self.matcher = matcher
        self.kb = kb
        self.policies = policies
        self.approvals = approvals
        self.actions = actions
        self._now = clock

    def _take(self, card_id: int, status: str, apply_existing: bool = False) -> ChatCard:
        """Flip a proposed card to `status` (row-locked on Postgres); a detached copy."""
        with self.sf.begin() as s:
            q = select(ChatCard).where(ChatCard.id == card_id)
            if s.get_bind().dialect.name == "postgresql":
                q = q.with_for_update()
            card = s.scalars(q).first()
            if card is None:
                raise LookupError("cartão não encontrado")
            if card.status != "proposed":
                raise CardConflict("este cartão já foi decidido")
            card.status = status
            card.decided_at = self._now()
            if card.kind == "rule":
                card.apply_existing = apply_existing
            s.flush()
            s.expunge(card)
            return card

    def _view(self, card_id: int) -> CardView:
        view = self.store.card(card_id)
        if view is None:
            raise LookupError("cartão não encontrado")
        return view

    def cancel(self, card_id: int) -> CardView:
        self._take(card_id, "cancelled")
        return self._view(card_id)

    def confirm(
        self,
        card_id: int,
        *,
        apply_existing: bool = False,
        verdict: Verdict | None = None,
    ) -> CardView:
        current = self.store.card(card_id)
        if current is None:
            raise LookupError("cartão não encontrado")
        if current.kind == "actions" and verdict is None and current.verdict is None:
            raise ValueError("escolha aprovar ou recusar")
        card = self._take(card_id, "confirmed", apply_existing)
        try:
            self._apply(card, verdict)
        except (_Applied, ValueError) as exc:
            with self.sf.begin() as s:
                row = s.get_one(ChatCard, card_id)
                row.status, row.error = "failed", str(exc)[:ERROR_MAX]
        return self._view(card_id)

    def _apply(self, card: ChatCard, verdict: Verdict | None) -> None:
        match card.kind:
            case "rule":
                self._apply_rule(card)
            case "preference":
                scope: KbScopeName = (
                    "decision" if card.kb_scope == "decision" else "classification"
                )
                entry = self.kb.add(scope, card.kb_text or "", channel="chat", source=card.channel)
                self._set(card.id, kb_entry_id=entry.id)
            case "promote":
                if card.rule_id is None:
                    raise _Applied("cartão sem regra")
                self.rules.promote(card.rule_id)
            case "policy":
                target = _TARGETS.get(card.policy_target or "")
                trigger = _TRIGGERS.get(card.policy_trigger or "")
                decision = _DECISIONS.get(card.policy_value or "")
                if target is None or trigger is None or decision is None:
                    raise _Applied("política inválida")
                self.policies.set(target, trigger, decision, channel="chat")
            case "actions":
                self._apply_actions(card, verdict)
            case _:
                raise CardConflict("este cartão só mostra informação")

    def _apply_rule(self, card: ChatCard) -> None:
        action = as_action_kind(card.action or "")
        if action is None:
            raise _Applied(f"ação desconhecida: {card.action}")
        rule_id = self.rules.add(
            name=card.rule_name or "regra do chat",
            action=action,
            sender_address=card.sender_address,
            sender_domain=card.sender_domain,
            category=card.category,
            subject_contains=card.subject_contains,
            unless_document=bool(card.unless_document),
            source="chat",
        )
        self._set(card.id, created_rule_id=rule_id)
        if card.apply_existing:
            self._apply_existing(card, rule_id, action)

    def _apply_existing(self, card: ChatCard, rule_id: int, action: ActionKind) -> None:
        cond = RuleCondition(
            sender_address=card.sender_address,
            sender_domain=card.sender_domain,
            category=card.category,
            subject_contains=card.subject_contains,
            unless_document=bool(card.unless_document),
        )
        ids, _ = self.matcher.existing(cond, APPLY_EXISTING_MAX)
        name = card.rule_name or "regra do chat"
        ref = RuleRef(rule_id=rule_id, name=name, mode="suggest")
        for email_id in ids:
            got = self.actions.propose(
                email_id, action, f"regra '{name}'", run_id=None, trigger="chat", rule=ref
            )
            if got.outcome in ("suggested", "auto"):
                self.matcher.record_fired(rule_id)

    def _apply_actions(self, card: ChatCard, verdict: Verdict | None) -> None:
        chosen = verdict or _VERDICTS.get(card.verdict or "")
        if chosen is None:
            raise _Applied("escolha aprovar ou recusar")
        with self.sf() as s:
            ids = [
                a for a in s.scalars(
                    select(ChatCardItem.action_id)
                    .where(ChatCardItem.card_id == card.id, ChatCardItem.action_id.is_not(None))
                    .order_by(ChatCardItem.position)
                ) if a is not None
            ]
        source: ChatChannel = "audio" if card.channel == "audio" else "chat"
        decide = self.approvals.approve if chosen == "approve" else self.approvals.reject
        decide(ids, card.comment, source=source)
        self._set(card.id, verdict=chosen)

    def _set(
        self,
        card_id: int,
        *,
        kb_entry_id: int | None = None,
        created_rule_id: int | None = None,
        verdict: str | None = None,
    ) -> None:
        with self.sf.begin() as s:
            row = s.get_one(ChatCard, card_id)
            if kb_entry_id is not None:
                row.kb_entry_id = kb_entry_id
            if created_rule_id is not None:
                row.created_rule_id = created_rule_id
            if verdict is not None:
                row.verdict = verdict
