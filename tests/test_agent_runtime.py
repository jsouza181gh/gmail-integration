from datetime import date

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from sqlalchemy.orm import Session, sessionmaker

from src.agent.executor import ToolExecutor
from src.agent.loop import AgentLoop
from src.agent.registry import AgentTool, Registry
from src.agent.state import AgentState, Classified, EmailFacts, StateUpdate
from src.agent.trail import Trail
from src.classification.taxonomy import Classification
from src.db.models import AgentConfig
from src.llm.types import ChatMessage, Prompt, Schema, ToolCallRequest
from tests.agent_fakes import FakeToolCaller, make_ctx, make_email, turn

CLS = Classification(
    category="Promoções",
    reason="oferta",
    is_relevant=False,
    deadline=None,
    has_document=False,
    ephemeral=True,
    category_set_id=1,
)


class PeekCtx(Schema):
    email_id: str


class PeekState(Schema):
    classification: Classification | None


SEEN: list[tuple[PeekCtx, PeekState]] = []


@tool(response_format="content_and_artifact", parse_docstring=True)
def peek(note: str, runtime: ToolRuntime[PeekCtx, PeekState]) -> tuple[str, StateUpdate | None]:
    """Look at the email under work.

    Args:
        note: free text echoed back.
    """
    SEEN.append((runtime.context, runtime.state))
    return f"{note}:{runtime.context.email_id}", None


@tool(response_format="content_and_artifact")
def mark(runtime: ToolRuntime[PeekCtx, PeekState]) -> tuple[str, StateUpdate | None]:
    """Classify the email under work as a promotion."""
    return "ok", Classified(email_id=runtime.context.email_id, classification=CLS)


@tool(response_format="content_and_artifact")
def explode(runtime: ToolRuntime[PeekCtx, PeekState]) -> tuple[str, StateUpdate | None]:
    """Always fails."""
    raise RuntimeError("kaboom")


@tool(response_format="content_and_artifact")
def secret(runtime: ToolRuntime[PeekCtx, PeekState]) -> tuple[str, StateUpdate | None]:
    """Only for planned calls."""
    return "planned", None


def _tool(t: object, *, planned: bool = False) -> AgentTool[PeekCtx, PeekState]:
    from langchain_core.tools import BaseTool

    assert isinstance(t, BaseTool)
    return AgentTool(
        t,
        context_of=lambda c: PeekCtx(email_id=c.email_id or ""),
        state_of=lambda c, s: PeekState(classification=s.emails["a"].classification),
        contexts=frozenset({"webhook"}),
        offered=frozenset() if planned else None,
    )


REGISTRY = Registry(
    [_tool(peek), _tool(mark), _tool(explode), _tool(secret, planned=True)]
)


@pytest.fixture
def trail(session_factory: sessionmaker[Session]) -> Trail:
    with session_factory.begin() as s:
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", chat_prompt="c", max_turns=3))
    return Trail(session_factory)


def _state() -> AgentState:
    return AgentState(emails={"a": EmailFacts(email=make_email("a"))})


def _call(name: str, call_id: str = "c1", **args: str) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=name, args=dict(args))


def test_tool_receives_only_its_slices(trail: Trail) -> None:
    SEEN.clear()
    run = trail.start_run("webhook", "a", 1)
    res = ToolExecutor(REGISTRY, trail).execute(
        _call("peek", note="oi"), make_ctx(run_id=run), _state(), source="model"
    )
    assert res.ok and res.message.content == "oi:a"
    assert res.message.role == "tool" and res.message.tool_call_id == "c1"
    ctx, state = SEEN[0]
    assert ctx == PeekCtx(email_id="a") and state == PeekState(classification=None)
    assert set(PeekCtx.model_fields) == {"email_id"}


def test_runtime_hidden_from_model_schema() -> None:
    from pydantic import BaseModel

    schema = peek.tool_call_schema
    assert isinstance(schema, type) and issubclass(schema, BaseModel)
    assert set(schema.model_json_schema()["properties"]) == {"note"}


def test_update_is_returned_and_reduced(trail: Trail) -> None:
    from src.agent.state import reduce

    run = trail.start_run("webhook", "a", 1)
    before = _state()
    res = ToolExecutor(REGISTRY, trail).execute(
        _call("mark"), make_ctx(run_id=run), before, source="model"
    )
    assert res.update == Classified(email_id="a", classification=CLS)
    after = reduce(before, res.update)
    assert after.emails["a"].classification == CLS
    assert before.emails["a"].classification is None


@pytest.mark.parametrize(
    ("call", "source", "fragment"),
    [
        (_call("nope"), "model", "não existe"),
        (_call("secret"), "model", "não disponível"),
        (_call("peek"), "model", "argumentos inválidos"),
        (_call("explode"), "model", "kaboom"),
    ],
)
def test_failures_become_error_messages(
    trail: Trail, call: ToolCallRequest, source: str, fragment: str
) -> None:
    run = trail.start_run("webhook", "a", 1)
    res = ToolExecutor(REGISTRY, trail).execute(
        call, make_ctx(run_id=run), _state(), source="model" if source == "model" else "planned"
    )
    assert not res.ok and res.update is None
    assert res.message.content.startswith("erro:") and fragment in res.message.content
    (step,) = trail.steps(run)
    assert (step.kind, step.name, step.status) == ("tool", call.name, "error")


def test_planned_only_tool_runs_when_planned(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    res = ToolExecutor(REGISTRY, trail).execute(
        _call("secret"), make_ctx(run_id=run), _state(), source="planned"
    )
    assert res.ok and res.message.content == "planned"


def test_tool_unavailable_for_trigger(trail: Trail) -> None:
    run = trail.start_run("chat", None, 1)
    res = ToolExecutor(REGISTRY, trail).execute(
        _call("peek", note="x"), make_ctx(run_id=run, trigger="chat"), _state(), source="model"
    )
    assert not res.ok and "não disponível" in res.message.content


def test_registry_offers_model_tools_for_trigger() -> None:
    assert [t.name for t in REGISTRY.for_model("webhook")] == ["peek", "mark", "explode"]
    assert REGISTRY.for_model("chat") == []


PROMPT = Prompt(purpose="decide", messages=(ChatMessage(role="human", content="decida"),))


def test_loop_stops_on_plain_answer(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    caller = FakeToolCaller([turn("", _call("mark")), turn(" nada a fazer ")])
    executor = ToolExecutor(REGISTRY, trail)
    res = AgentLoop(executor, trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=3
    )
    assert res.status == "done" and res.summary == "nada a fazer"
    assert res.state.emails["a"].classification == CLS and res.state.turns_used == 2
    steps = trail.steps(run)
    assert [(s.position, s.kind, s.name) for s in steps] == [
        (0, "llm", "decide"),
        (1, "tool", "mark"),
        (2, "llm", "decide"),
    ]
    second = caller.prompts[1].messages
    assert [m.role for m in second] == ["human", "ai", "tool"]
    assert second[1].tool_calls[0].name == "mark" and second[2].tool_call_id == "c1"
    assert caller.offered[0] == ["peek", "mark", "explode"]


def test_loop_truncates_at_max_turns(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    caller = FakeToolCaller([turn("", _call("peek", f"c{i}", note=str(i))) for i in range(3)])
    res = AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=3
    )
    assert res.status == "truncated" and len(caller.prompts) == 3


def test_loop_refuses_repeated_identical_call(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    SEEN.clear()
    caller = FakeToolCaller(
        [
            turn("", _call("peek", "c1", note="x")),
            turn("", _call("peek", "c2", note="x")),
            turn("fim"),
        ]
    )
    AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=4
    )
    assert len(SEEN) == 1
    tool_msg = caller.prompts[2].messages[-1]
    assert tool_msg.content.startswith("erro:") and "repetida" in tool_msg.content
    assert [s.status for s in trail.steps(run) if s.kind == "tool"] == ["ok", "error"]


def test_llm_step_id_travels_in_call_context(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    caller = FakeToolCaller([turn("fim")])
    AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=2
    )
    (step,) = trail.steps(run)
    ctx = caller.prompts[0].context
    assert (ctx.agent_run_id, ctx.agent_step_id, ctx.email_ids) == (run, step.id, ("a",))


def test_llm_failure_closes_step_and_raises(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    caller = FakeToolCaller([RuntimeError("gemini down")])
    with pytest.raises(RuntimeError):
        AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
            make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=2
        )
    (step,) = trail.steps(run)
    assert step.status == "error" and "gemini down" in (step.detail or "")


def test_run_lifecycle(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    trail.finish_run(run, "done", summary="arquivar sugerido", error=None)
    row = trail.run(run)
    assert (row.status, row.summary, row.email_id) == ("done", "arquivar sugerido", "a")
    assert row.finished_at is not None


def test_deadline_type_is_calendar_date() -> None:
    assert CLS.model_copy(update={"deadline": date(2026, 11, 29)}).deadline == date(2026, 11, 29)


def test_loop_carries_thought_signatures_to_next_turn(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    first = turn("", _call("mark")).model_copy(update={"signatures": {"c1": "c2ln"}})
    caller = FakeToolCaller([first, turn("fim")])
    AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=3
    )
    assert caller.prompts[1].messages[1].signatures == {"c1": "c2ln"}


def test_loop_calls_on_tool(trail: Trail) -> None:
    run = trail.start_run("webhook", "a", 1)
    caller = FakeToolCaller([turn("", _call("peek", "c1", note="1")), turn("fim")])
    seen: list[str] = []
    AgentLoop(ToolExecutor(REGISTRY, trail), trail, caller).run(
        make_ctx(run_id=run, caller=caller), _state(), PROMPT, max_turns=3, on_tool=seen.append
    )
    assert seen == ["peek"]
