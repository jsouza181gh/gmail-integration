from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.agent.executor import ToolExecution, ToolExecutor
from src.agent.sender_history import DbSenderHistory
from src.agent.state import AgentState, BodyFetched, Classified, EmailFacts, SenderLoaded
from src.agent.tools import build_registry
from src.agent.trail import Trail
from src.agent.types import LabelInfo, MailItem, MailPage, MailQuery, SenderStats, Trigger
from src.classification.classifier import BatchResult, Item
from src.classification.seed import V1
from src.classification.taxonomy import Taxonomy
from src.db.models import AgentConfig, Email
from src.llm.types import JsonArgs, Prompt, StructuredResult, TokenUsage, ToolCallRequest
from tests.agent_fakes import make_ctx, make_email

TAXONOMY = Taxonomy(set_id=1, version=1, spec=V1)


class FakeTaxonomy:
    def active_taxonomy(self) -> Taxonomy | None:
        return TAXONOMY

    def active_preferences(self, scope: str) -> list[str]:
        return ["vagas: backend python"]


class FakeStructured:
    def __init__(self, items: Sequence[Item]) -> None:
        self.items = list(items)
        self.prompts: list[Prompt] = []

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T]
    ) -> StructuredResult[T]:
        self.prompts.append(prompt)
        parsed = schema.model_validate(BatchResult(items=self.items).model_dump())
        return StructuredResult[T](parsed=parsed, model="m1", usage=TokenUsage())


class FakeMail:
    def __init__(self) -> None:
        self.bodies: list[str] = []
        self.queries: list[MailQuery] = []

    def list_messages(self, query: MailQuery) -> MailPage:
        self.queries.append(query)
        item = MailItem(
            id="x", thread_id="t", sender="a@b.c", subject="s", date="d", snippet="n", labels=()
        )
        return MailPage(items=(item,), next_page_token=None, result_size_estimate=1)

    def get_body(self, email_id: str) -> str:
        self.bodies.append(email_id)
        return "corpo do e-mail"

    def list_labels(self) -> tuple[LabelInfo, ...]:
        return (LabelInfo(id="INBOX", name="INBOX", type="system"),)


class FakeHistory:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def stats(self, address: str) -> SenderStats:
        self.asked.append(address)
        return SenderStats(address=address, total=3)


def _item(**over: object) -> Item:
    base = Item(
        id="a",
        category="Promoções",
        reason="oferta de loja",
        is_relevant=False,
        deadline="2026-11-29",
        has_document=False,
        ephemeral=True,
    )
    return base.model_copy(update=over)


@pytest.fixture
def trail(session_factory: sessionmaker[Session]) -> Trail:
    with session_factory.begin() as s:
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", max_turns=3))
    return Trail(session_factory)


def _state(**facts: object) -> AgentState:
    return AgentState(emails={"a": EmailFacts(email=make_email("a")).model_copy(update=facts)})


def _exec(
    trail: Trail,
    name: str,
    *,
    state: AgentState | None = None,
    args: JsonArgs | None = None,
    trigger: Trigger = "webhook",
    model: bool = False,
    **deps: object,
) -> tuple[ToolExecution, int]:
    run = trail.start_run(trigger, "a", 1)
    ctx = make_ctx(run_id=run, trigger=trigger, extra=deps)
    call = ToolCallRequest(id="c1", name=name, args=args or {})
    res = ToolExecutor(build_registry(), trail).execute(
        call, ctx, state or _state(), source="model" if model else "planned"
    )
    return res, run


# --- classify_email -----------------------------------------------------------

def test_classify_email_returns_classified_update(trail: Trail) -> None:
    llm = FakeStructured([_item()])
    res, _ = _exec(trail, "classify_email", llm=_llm(llm), taxonomy=FakeTaxonomy())
    assert res.ok
    assert isinstance(res.update, Classified) and res.update.email_id == "a"
    c = res.update.classification
    assert (c.category, c.deadline, c.category_set_id) == ("Promoções", date(2026, 11, 29), 1)
    assert "Promoções" in res.message.content


def test_classify_email_uses_runtime_call_context(trail: Trail) -> None:
    llm = FakeStructured([_item()])
    _, run = _exec(trail, "classify_email", llm=_llm(llm), taxonomy=FakeTaxonomy())
    (step,) = trail.steps(run)
    ctx = llm.prompts[0].context
    assert (ctx.agent_run_id, ctx.agent_step_id, ctx.email_ids) == (run, step.id, ("a",))
    system = llm.prompts[0].messages[0].content
    assert "vagas: backend python" in system


def test_classify_email_prefers_body_from_state(trail: Trail) -> None:
    llm = FakeStructured([_item()])
    _exec(
        trail, "classify_email", state=_state(body="CORPO COMPLETO"),
        llm=_llm(llm), taxonomy=FakeTaxonomy(),
    )
    assert "CORPO COMPLETO" in llm.prompts[0].messages[1].content


def test_classify_email_invalid_category_is_error_not_update(trail: Trail) -> None:
    llm = FakeStructured([_item(category="Urgente")])
    res, _ = _exec(trail, "classify_email", llm=_llm(llm), taxonomy=FakeTaxonomy())
    assert not res.ok and res.update is None


def test_classify_email_not_offered_to_webhook_model(trail: Trail) -> None:
    res, _ = _exec(trail, "classify_email", model=True)
    assert not res.ok and "não disponível" in res.message.content


# --- get_email_body -----------------------------------------------------------

def test_get_email_body_fetches_and_updates(trail: Trail) -> None:
    mail = FakeMail()
    res, _ = _exec(trail, "get_email_body", model=True, mail=mail)
    assert res.ok and mail.bodies == ["a"]
    assert res.update == BodyFetched(email_id="a", body="corpo do e-mail")
    assert "<email_data>" in res.message.content and "corpo do e-mail" in res.message.content


def test_get_email_body_uses_state_before_gmail(trail: Trail) -> None:
    mail = FakeMail()
    res, _ = _exec(trail, "get_email_body", model=True, state=_state(body="já tenho"), mail=mail)
    assert res.ok and mail.bodies == [] and res.update is None
    assert "já tenho" in res.message.content


# --- sender_history -----------------------------------------------------------

def test_sender_history_extracts_address(trail: Trail) -> None:
    history = FakeHistory()
    res, _ = _exec(trail, "sender_history", history=history)
    assert res.ok and history.asked == ["ofertas@loja.com"]
    assert res.update == SenderLoaded(
        email_id="a", stats=SenderStats(address="ofertas@loja.com", total=3)
    )


def test_db_sender_history_counts_categories(session_factory: sessionmaker[Session]) -> None:
    seen = datetime(2026, 11, 18, tzinfo=UTC)
    with session_factory.begin() as s:
        for i, cat in enumerate(["Promoções", "Promoções", "Compras"]):
            s.add(Email(id=f"e{i}", sender="Loja <Ofertas@Loja.com>", category=cat,
                        attempts=0, received_at=seen))
        s.add(Email(id="other", sender="x@y.z", category="Pessoas", attempts=0))
    stats = DbSenderHistory(session_factory).stats("ofertas@loja.com")
    assert stats.total == 3
    assert [(c.category, c.count) for c in stats.by_category] == [("Promoções", 2), ("Compras", 1)]
    assert stats.last_seen is not None and stats.last_seen.replace(tzinfo=None) == seen.replace(
        tzinfo=None
    )


# --- chat-only read tools -------------------------------------------------------

def test_list_emails_only_in_chat(trail: Trail) -> None:
    res, _ = _exec(trail, "list_emails", model=True, args={"query": "is:unread"})
    assert not res.ok
    mail = FakeMail()
    res, _ = _exec(
        trail, "list_emails", model=True, trigger="chat", args={"query": "is:unread"}, mail=mail
    )
    assert res.ok and mail.queries == [MailQuery(query="is:unread")]
    assert '"id":"x"' in res.message.content


def test_list_emails_caps_max_results(trail: Trail) -> None:
    mail = FakeMail()
    _exec(trail, "list_emails", model=True, trigger="chat", args={"max_results": 500}, mail=mail)
    assert mail.queries[0].max_results == 100


def test_list_labels_in_chat(trail: Trail) -> None:
    res, _ = _exec(trail, "list_labels", model=True, trigger="chat", mail=FakeMail())
    assert res.ok and "INBOX" in res.message.content


# --- registry -----------------------------------------------------------------

EXPECTED_SLICES = {
    "classify_email": ({"llm", "taxonomy", "call"}, {"email", "body"}),
    "get_email_body": ({"mail", "email_id"}, {"body"}),
    "sender_history": ({"history"}, {"email_id", "sender"}),
    "list_emails": ({"mail"}, set()),
    "list_labels": ({"mail"}, set()),
}


def test_tool_slices_are_minimal() -> None:
    from src.agent.tools import SLICES

    assert {n: (set(c.model_fields), set(s.model_fields)) for n, (c, s) in SLICES.items()} == (
        EXPECTED_SLICES
    )


def test_registry_offers_per_trigger() -> None:
    reg = build_registry()
    assert {t.name for t in reg.for_model("webhook")} == {"get_email_body"}
    assert {t.name for t in reg.for_model("chat")} == set(EXPECTED_SLICES)


def _llm(structured: FakeStructured) -> object:
    from src.agent.context import LLMDeps
    from tests.agent_fakes import FakeToolCaller

    return LLMDeps(structured=structured, tools=FakeToolCaller([]))
