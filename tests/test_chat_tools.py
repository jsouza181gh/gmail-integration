"""Chat card tools: they only ever write cards; the user's click applies them."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.context import ChatScope
from src.agent.executor import ToolExecution, ToolExecutor
from src.agent.policy import PolicyResolver
from src.agent.rules import RuleAdmin
from src.agent.state import AgentState
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.chat.retrieval import DbDocCatalog, DbEmailLookup
from src.chat.store import ChatStore
from src.db.models import (
    AgentConfig,
    Attachment,
    AutomationRule,
    Email,
    KbEntry,
    SuggestedAction,
    ToolPolicy,
)
from src.jobs.store import JobStore
from src.llm.types import JsonArgs, ToolCallRequest
from tests.agent_fakes import FakeTaxonomy, make_ctx
from tests.chat_fakes import SpamMail, add_email, spam_item


def _run(trail: Trail, name: str, args: JsonArgs | None, deps: dict[str, object]) -> ToolExecution:
    run = trail.start_run("chat", None, 1)
    ctx = make_ctx(run_id=run, trigger="chat", email_id=None, extra=deps)
    call = ToolCallRequest(id="c1", name=name, args=args or {})
    return ToolExecutor(build_registry(), trail).execute(
        call, ctx, AgentState(emails={}), source="model"
    )


class ChatWorld:
    """Real DB adapters for the card tools, plus one chat turn to attach cards to."""

    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.sf = sf
        jobs = JobStore(sf)
        self.store = ChatStore(sf)
        self.rules = RuleAdmin(sf)
        self.actions = ActionService(sf, jobs, PolicyResolver(sf))
        self.approvals = ApprovalService(sf, jobs)
        conv = self.store.create_conversation()
        self.message_id = self.store.start_turn(conv.id, text="oi").assistant.id
        self.trail = Trail(sf)
        self.mail = SpamMail([spam_item("sp1"), spam_item("sp2", "Alerta do Google - Petr4")])
        self.deps: dict[str, object] = {
            "chat": ChatScope(conversation_id=conv.id, message_id=self.message_id,
                              channel="audio"),
            "cards": self.store,
            "refs": self.store,
            "rules": self.rules,
            "actions": self.actions,
            "pending": self.approvals,
            "lookup": DbEmailLookup(sf, self.mail),
            "mail": self.mail,
            "docs": DbDocCatalog(sf),
            "taxonomy": FakeTaxonomy(),
        }

    def run(self, name: str, args: JsonArgs | None = None) -> ToolExecution:
        return _run(self.trail, name, args, self.deps)

    def cards(self) -> list[str]:
        return [c.kind for c in self.store.turn(self.message_id).assistant.cards]

    def count(self, model: type[object]) -> int:
        with self.sf() as s:
            return s.scalar(select(func.count()).select_from(model)) or 0

    def pending_action(self, email_id: str, kind: str = "trash") -> int:
        ref = self.actions.propose(email_id, "trash" if kind == "trash" else "archive", "x",
                                   run_id=None, trigger="webhook")
        assert ref.action_id is not None
        return ref.action_id


@pytest.fixture
def w(session_factory: sessionmaker[Session]) -> ChatWorld:
    with session_factory.begin() as s:
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", chat_prompt="c",
                          max_turns=3))
    add_email(session_factory, "e1")
    add_email(session_factory, "e2")
    add_email(session_factory, "nf", has_document=True)
    return ChatWorld(session_factory)


def test_propose_tools_never_write_config(w: ChatWorld) -> None:
    rid = w.rules.add(name="arq", action="archive", sender_domain="loja.com")
    a1 = w.pending_action("e1")
    calls: list[tuple[str, JsonArgs]] = [
        ("propose_rule", {"name": "lixo", "action": "trash", "sender_domain": "loja.com"}),
        ("propose_preference", {"text": "vagas Java não", "scope": "decision"}),
        ("propose_promotion", {"rule_id": rid}),
        ("propose_policy", {"target": "archive", "trigger": "webhook", "policy": "auto"}),
        ("decide_pending", {"action_ids": [a1], "verdict": "approve", "comment": "ok"}),
    ]
    for name, args in calls:
        res = w.run(name, args)
        assert res.ok, res.message.content
        assert "aguardando confirmação" in res.message.content
    assert w.cards() == ["rule", "preference", "promote", "policy", "actions"]
    assert w.count(AutomationRule) == 1 and w.count(KbEntry) == 0 and w.count(ToolPolicy) == 0
    with w.sf() as s:
        assert s.get_one(SuggestedAction, a1).status == "pending"
    rule = w.rules.get(rid)
    assert rule is not None and rule.mode == "suggest"


def test_propose_rule_counts_affected(w: ChatWorld) -> None:
    res = w.run("propose_rule", {"name": "lixo", "action": "trash", "sender_domain": "@Loja.com",
                                 "unless_document": True})
    assert res.ok and "afeta ~2 e-mail" in res.message.content
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert card.affected == 2 and card.rule is not None
    assert card.rule.sender_domain == "loja.com" and card.channel == "audio"


def test_propose_rule_rejects_unknown_category(w: ChatWorld) -> None:
    res = w.run("propose_rule", {"name": "x", "action": "archive", "category": "Inexistente"})
    assert not res.ok and "Promoções" in res.message.content
    assert w.run("propose_rule", {"name": "x", "action": "archive", "category": "promoções"}).ok
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert card.rule is not None and card.rule.category == "Promoções"


def test_propose_rule_needs_a_condition(w: ChatWorld) -> None:
    assert not w.run("propose_rule", {"name": "x", "action": "archive"}).ok
    assert w.cards() == []


def test_propose_promotion_refuses_block_sender(w: ChatWorld) -> None:
    rid = w.rules.add(name="bloq", action="block_sender", sender_domain="spam.com")
    assert not w.run("propose_promotion", {"rule_id": rid}).ok
    assert w.cards() == []


def test_propose_policy_refuses_auto_block_sender(w: ChatWorld) -> None:
    res = w.run("propose_policy", {"target": "block_sender", "trigger": "chat", "policy": "auto"})
    assert not res.ok and w.cards() == []


def test_decide_pending_drops_decided_ids(w: ChatWorld) -> None:
    a1, a2 = w.pending_action("e1"), w.pending_action("e2")
    w.approvals.reject([a1])
    assert w.run("decide_pending", {"action_ids": [a1, a2], "verdict": "reject"}).ok
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert [i.action_id for i in card.items] == [a2] and card.verdict == "reject"
    assert not w.run("decide_pending", {"action_ids": [a1], "verdict": "reject"}).ok


def test_list_pending_summarizes_and_cards(w: ChatWorld) -> None:
    empty = w.run("list_pending")
    assert empty.ok and "nenhuma" in empty.message.content and w.cards() == []
    w.pending_action("e1")
    w.pending_action("e2", "archive")
    res = w.run("list_pending")
    assert res.ok and "2 pendente" in res.message.content and w.cards() == ["actions"]


def test_batch_one_card_for_many(w: ChatWorld) -> None:
    for i in range(3, 6):
        add_email(w.sf, f"e{i}")
    res = w.run("propose_email_actions", {"kind": "archive", "reason": "pedido do usuário",
                                          "email_ids": ["e1", "e2", "e3", "e4", "e5"]})
    assert res.ok and "archive em 5 e-mail(s)" in res.message.content
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert card.kind == "actions" and len(card.items) == 5


def test_batch_mixed_outcomes(w: ChatWorld) -> None:
    w.pending_action("e2")
    res = w.run("propose_email_actions", {"kind": "trash", "reason": "lixo",
                                          "email_ids": ["e1", "e2", "gone"]})
    text = res.message.content
    assert res.ok and "trash em 2 e-mail(s)" in text
    assert "já pendentes: 1" in text and "não existem mais no Gmail: 1" in text
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert len(card.items) == 2


def test_batch_denied_by_policy_is_counted(w: ChatWorld) -> None:
    with w.sf.begin() as s:
        s.add(ToolPolicy(target="trash", trigger="chat", policy="deny"))
    res = w.run("propose_email_actions", {"kind": "trash", "reason": "lixo",
                                          "email_ids": ["e1", "e2"]})
    assert not res.ok and "negados pela política: 2" in res.message.content
    assert w.cards() == []


def test_batch_by_gmail_query_caps_at_50(w: ChatWorld) -> None:
    w.mail.items = {f"q{i}": spam_item(f"q{i}") for i in range(120)}
    w.mail.page_size = 40
    res = w.run("propose_email_actions", {"kind": "trash", "reason": "spam",
                                          "gmail_query": "in:spam from:googlealerts-noreply@google.com"})
    assert res.ok and "trash em 50 e-mail(s)" in res.message.content
    assert w.mail.queries[0].include_spam_trash is True
    assert w.count(SuggestedAction) == 50


def test_batch_requires_exactly_one_source(w: ChatWorld) -> None:
    both = w.run("propose_email_actions", {"kind": "trash", "reason": "x", "email_ids": ["e1"],
                                           "gmail_query": "in:spam"})
    neither = w.run("propose_email_actions", {"kind": "trash", "reason": "x"})
    assert not both.ok and not neither.ok
    assert "informe email_ids ou gmail_query" in neither.message.content


def test_propose_email_action_is_gone() -> None:
    assert build_registry().get("propose_email_action") is None


def test_show_emails_and_share_documents(w: ChatWorld) -> None:
    with w.sf.begin() as s:
        s.add_all([
            Attachment(email_id="e1", part_id="1", filename="b.pdf", mime_type="application/pdf",
                       size=1, status="done", title="Boleto"),
            Attachment(email_id="e1", part_id="2", filename="c.pdf", mime_type="application/pdf",
                       size=1, status="pending"),
        ])
    assert w.run("show_emails", {"email_ids": ["e1", "zz"]}).ok
    res = w.run("share_documents", {"attachment_ids": [1, 2]})
    assert res.ok and "#2" in res.message.content  # reported as unavailable
    emails, docs = w.store.turn(w.message_id).assistant.cards
    assert [i.email_id for i in emails.items] == ["e1"]
    assert [i.attachment_id for i in docs.items] == [1]
    assert not w.run("share_documents", {"attachment_ids": [2]}).ok


def test_card_tools_need_a_chat_turn(w: ChatWorld) -> None:
    deps = {k: v for k, v in w.deps.items() if k != "chat"}
    res = _run(w.trail, "propose_preference", {"text": "x", "scope": "decision"}, deps)
    assert not res.ok and w.cards() == []


def test_chat_registry_offers_only_chat_tools() -> None:
    reg = build_registry()
    webhook = {t.name for t in reg.for_model("webhook")}
    chat = {t.name for t in reg.for_model("chat")}
    assert webhook == {"get_email_body", "propose_action"}
    assert chat == {
        "list_emails", "list_labels", "search_mail", "read_email", "find_documents",
        "list_rules", "sender_stats", "show_emails", "share_documents", "list_pending",
        "decide_pending", "propose_email_actions", "propose_rule", "propose_preference",
        "propose_promotion", "propose_policy",
    }
    for name in ("classify_email", "get_email_body", "sender_history", "propose_action"):
        entry = reg.get(name)
        assert entry is not None and entry.contexts == frozenset({"webhook"})


def test_ensure_fetches_and_stores_unknown_id(w: ChatWorld) -> None:
    row = DbEmailLookup(w.sf, w.mail).ensure("sp1")
    assert row is not None and row.source == "chat"
    assert row.sender == "Google Alerts <googlealerts-noreply@google.com>"
    with w.sf() as s:
        stored = s.get(Email, "sp1")
        assert stored is not None and stored.source == "chat" and stored.received_at is not None


def test_ensure_unknown_in_gmail_returns_none(w: ChatWorld) -> None:
    assert DbEmailLookup(w.sf, w.mail).ensure("gone") is None


def test_show_emails_accepts_spam_ids(w: ChatWorld) -> None:
    res = w.run("show_emails", {"email_ids": ["sp1", "sp2"]})
    assert res.ok and "2 e-mail(s)" in res.message.content
    (card,) = w.store.turn(w.message_id).assistant.cards
    assert [i.email_id for i in card.items] == ["sp1", "sp2"]


def test_show_emails_gone_id_message(w: ChatWorld) -> None:
    res = w.run("show_emails", {"email_ids": ["gone"]})
    assert not res.ok and "não existe mais no Gmail" in res.message.content


def test_read_email_of_spam_id_reads_gmail_body(w: ChatWorld) -> None:
    res = w.run("read_email", {"email_id": "sp1"})
    assert res.ok and "corpo de sp1" in res.message.content


def _refs(w: ChatWorld) -> list[str]:
    from src.db.models import ChatMessageRef

    with w.sf() as s:
        return [r.email_id for r in s.query(ChatMessageRef).order_by(ChatMessageRef.position)]


def test_list_emails_records_refs_only_in_chat(w: ChatWorld) -> None:
    deps = {k: v for k, v in w.deps.items() if k != "chat"}
    assert _run(w.trail, "list_emails", {"query": "in:spam"}, deps).ok
    assert _refs(w) == []
    assert w.run("list_emails", {"query": "in:spam"}).ok
    assert _refs(w) == ["sp1", "sp2"]


def test_show_and_search_record_refs(w: ChatWorld) -> None:
    assert w.run("show_emails", {"email_ids": ["e1", "sp1"]}).ok
    assert _refs(w) == ["e1", "sp1"]


def test_propose_rule_unknown_sender_suggests_close_match(w: ChatWorld) -> None:
    add_email(w.sf, "ga", sender="Google Alerts <googlealerts-noreply@google.com>")
    res = w.run("propose_rule", {"name": "alertas", "action": "trash",
                                 "sender_address": "google-alerts-noreply@google.com"})
    assert not res.ok
    assert "nenhum e-mail de google-alerts-noreply@google.com" in res.message.content
    assert "parecido: googlealerts-noreply@google.com" in res.message.content
    ok = w.run("propose_rule", {"name": "alertas", "action": "trash",
                                "sender_address": "googlealerts-noreply@google.com"})
    assert ok.ok


def test_chat_prompt_v2_mentions_batch_tool_only() -> None:
    from src.agent.prompts import CHAT_PROMPT_V2

    assert "propose_email_actions" in CHAT_PROMPT_V2
    assert "propose_email_action " not in CHAT_PROMPT_V2
    assert "propose_email_action(" not in CHAT_PROMPT_V2
    assert "[evento]" in CHAT_PROMPT_V2


def test_propose_rule_unseen_sender_without_close_match_is_allowed(w: ChatWorld) -> None:
    res = w.run("propose_rule", {"name": "novo", "action": "trash",
                                 "sender_address": "promo@desconhecido.com.br"})
    assert res.ok and "nenhum e-mail de promo@desconhecido.com.br guardado ainda" in res.message.content
    assert w.cards() == ["rule"]


def test_list_emails_in_chat_caps_at_25(w: ChatWorld) -> None:
    w.mail.items = {f"q{i}": spam_item(f"q{i}") for i in range(60)}
    assert w.run("list_emails", {"query": "in:spam", "max_results": 100}).ok
    assert w.mail.queries[-1].max_results == 25
