import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.kb import KbAdmin
from src.agent.policy import PolicyAdmin, PolicyResolver
from src.agent.rules import RuleAdmin, RuleEngine
from src.agent.types import (
    ActionsDraft,
    CardDraft,
    ChatChannel,
    EmailsDraft,
    PolicyDraft,
    PreferenceDraft,
    PromoteDraft,
    RuleDraft,
)
from src.chat.cards import CardService
from src.chat.store import CardConflict, ChatStore
from src.db.models import (
    AutomationRule,
    Feedback,
    KbEntry,
    SuggestedAction,
    ToolPolicy,
)
from src.jobs.store import JobStore
from tests.chat_fakes import add_email


class World:
    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.sf = sf
        self.store = ChatStore(sf)
        jobs = JobStore(sf)
        self.rules = RuleAdmin(sf)
        self.approvals = ApprovalService(sf, jobs)
        self.actions = ActionService(sf, jobs, PolicyResolver(sf))
        self.cards = CardService(
            store=self.store, rules=self.rules, matcher=RuleEngine(sf), kb=KbAdmin(sf),
            policies=PolicyAdmin(sf), approvals=self.approvals, actions=self.actions,
        )
        conv = self.store.create_conversation()
        self.assistant_id = self.store.start_turn(conv.id, text="oi").assistant.id

    def card(self, draft: CardDraft, channel: ChatChannel = "chat") -> int:
        return self.store.add_card(self.assistant_id, channel, draft)

    def count[T](self, model: type[T]) -> int:
        with self.sf() as s:
            return s.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def w(session_factory: sessionmaker[Session]) -> World:
    return World(session_factory)


def _rule_draft() -> RuleDraft:
    return RuleDraft(name="lixo da loja", action="trash", sender_domain="loja.com",
                     unless_document=True, affected=2)


def test_confirm_rule_creates_suggest_rule_and_applies_existing(w: World) -> None:
    add_email(w.sf, "e1")
    add_email(w.sf, "e2")
    add_email(w.sf, "nf", has_document=True)
    view = w.cards.confirm(w.card(_rule_draft()), apply_existing=True)
    assert view.status == "confirmed" and view.created_rule_id is not None
    rule = w.rules.get(view.created_rule_id)
    assert rule is not None and (rule.mode, rule.unless_document) == ("suggest", True)
    with w.sf() as s:
        made = s.scalars(select(SuggestedAction)).all()
        assert {(a.email_id, a.status, a.origin) for a in made} == {
            ("e1", "pending", "rule"), ("e2", "pending", "rule"),
        }
        assert s.get_one(AutomationRule, view.created_rule_id).source == "chat"


def test_confirm_rule_without_apply_creates_no_suggestions(w: World) -> None:
    add_email(w.sf, "e1")
    w.cards.confirm(w.card(_rule_draft()))
    assert w.count(SuggestedAction) == 0


def test_confirm_is_single_shot(w: World) -> None:
    card_id = w.card(_rule_draft())
    w.cards.confirm(card_id)
    with pytest.raises(CardConflict):
        w.cards.confirm(card_id)
    other = w.card(_rule_draft())
    assert w.cards.cancel(other).status == "cancelled"
    with pytest.raises(CardConflict):
        w.cards.confirm(other)
    assert w.count(AutomationRule) == 1


def test_confirm_preference_kb_source_audio(w: World) -> None:
    card_id = w.card(PreferenceDraft(scope="decision", text="vagas de Java não me interessam"),
                     channel="audio")
    view = w.cards.confirm(card_id)
    with w.sf() as s:
        entry = s.get_one(KbEntry, view.kb_entry_id)
        assert (entry.scope, entry.source, entry.text) == (
            "decision", "audio", "vagas de Java não me interessam"
        )


def test_confirm_promote_fails_for_block_sender(w: World) -> None:
    rule_id = w.rules.add(name="bloq", action="block_sender", sender_domain="spam.com")
    view = w.cards.confirm(w.card(PromoteDraft(rule_id=rule_id)))
    assert view.status == "failed" and "nunca" in (view.error or "")
    rule = w.rules.get(rule_id)
    assert rule is not None and rule.mode == "suggest"


def test_confirm_promote_sets_auto(w: World) -> None:
    rule_id = w.rules.add(name="arq", action="archive", sender_domain="loja.com")
    assert w.cards.confirm(w.card(PromoteDraft(rule_id=rule_id))).status == "confirmed"
    rule = w.rules.get(rule_id)
    assert rule is not None and rule.mode == "auto"


def test_confirm_policy_refuses_never_auto(w: World) -> None:
    view = w.cards.confirm(w.card(PolicyDraft(target="block_sender", trigger="chat",
                                              policy="auto")))
    assert view.status == "failed"
    assert w.count(ToolPolicy) == 0
    ok = w.cards.confirm(w.card(PolicyDraft(target="archive", trigger="webhook", policy="auto")))
    assert ok.status == "confirmed" and w.count(ToolPolicy) == 1


def test_confirm_actions_skips_decided(w: World) -> None:
    add_email(w.sf, "e1")
    add_email(w.sf, "e2")
    a1 = w.actions.propose("e1", "trash", "lixo", run_id=None, trigger="webhook").action_id
    a2 = w.actions.propose("e2", "trash", "lixo", run_id=None, trigger="webhook").action_id
    assert a1 is not None and a2 is not None
    w.approvals.reject([a1])
    card_id = w.card(ActionsDraft(action_ids=(a1, a2), comment="não quero isso"), channel="audio")
    view = w.cards.confirm(card_id, verdict="approve")
    assert view.status == "confirmed"
    with w.sf() as s:
        assert s.get_one(SuggestedAction, a1).status == "rejected"
        assert s.get_one(SuggestedAction, a2).status == "approved"
        fb = s.scalars(select(Feedback).where(Feedback.action_id == a2)).one()
        assert (fb.source, fb.text) == ("audio", "não quero isso")


def test_confirm_actions_needs_a_verdict(w: World) -> None:
    with pytest.raises(ValueError):
        w.cards.confirm(w.card(ActionsDraft(action_ids=())))


def test_info_card_cannot_be_confirmed(w: World) -> None:
    add_email(w.sf, "e1")
    card_id = w.card(EmailsDraft(email_ids=("e1",)))
    with pytest.raises(CardConflict):
        w.cards.confirm(card_id)
    with pytest.raises(CardConflict):
        w.cards.cancel(card_id)
