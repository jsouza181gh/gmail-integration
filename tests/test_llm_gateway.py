from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ValidationError

from src.llm.config import LLMConfig, load_llm_config
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.gateway import LLMGateway
from src.llm.types import (
    CallContext,
    CallRecord,
    ChatMessage,
    Prompt,
    TokenUsage,
    ToolCallRequest,
)


class Out(BaseModel):
    x: int


Step = Out | Exception | None


class FakeRunnable:
    def __init__(self, script: list[Step]):
        self.script = script

    def invoke(self, messages: list[BaseMessage], /) -> object:
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        content = step.model_dump_json() if step else "garbage"
        raw = AIMessage(
            content=content,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return {"raw": raw, "parsed": step, "parsing_error": None}


Turn = AIMessage | Exception | str  # str: not an AIMessage (bad output)


class FakeToolRunnable:
    def __init__(self, script: list[Turn]):
        self.script = script
        self.seen: list[list[BaseMessage]] = []

    def invoke(self, messages: list[BaseMessage], /) -> object:
        self.seen.append(messages)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeChat:
    def __init__(self, script: list[Step], turns: list[Turn] | None = None):
        self.runnable = FakeRunnable(script)
        self.tool_runnable = FakeToolRunnable(turns or [])
        self.bound: list[str] = []

    def bind_tools(self, tools: Sequence[BaseTool]) -> FakeToolRunnable:
        self.bound = [t.name for t in tools]
        return self.tool_runnable

    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool
    ) -> FakeRunnable:
        assert schema is Out and include_raw
        return self.runnable


class ListRecorder:
    def __init__(self) -> None:
        self.items: list[CallRecord] = []

    def record(self, rec: CallRecord) -> None:
        self.items.append(rec)


class BrokenRecorder:
    def record(self, rec: CallRecord) -> None:
        raise RuntimeError("db down")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.t += seconds


P = Prompt(purpose="test", messages=(ChatMessage(role="human", content="oi"),))


def gw(
    *,
    chats: dict[str, FakeChat],
    models: Sequence[str] = ("m1",),
    recorder: ListRecorder | BrokenRecorder | None = None,
    sleeps: list[float] | None = None,
    clock: FakeClock | None = None,
    mode: str = "fast",
    rpm: int = 15,
    max_attempts: int = 8,
) -> LLMGateway:
    sleep_list = sleeps if sleeps is not None else []
    return LLMGateway(
        models,
        mode="free" if mode == "free" else "fast",
        rpm=rpm,
        max_attempts=max_attempts,
        recorder=recorder or ListRecorder(),
        chat_factory=chats.__getitem__,
        sleep=clock.sleep if clock else sleep_list.append,
        clock=clock.now if clock else (lambda: 0.0),
    )


def test_structured_output_returns_parsed_and_usage() -> None:
    res = gw(chats={"m1": FakeChat([Out(x=1)])}).generate_structured(P, Out)
    assert res.parsed == Out(x=1) and res.model == "m1"
    assert res.usage == TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def test_every_attempt_is_recorded() -> None:
    rec = ListRecorder()
    chat = FakeChat([RuntimeError("503 UNAVAILABLE"), Out(x=1)])
    gw(chats={"m1": chat}, recorder=rec).generate_structured(P, Out)
    assert [r.status for r in rec.items] == ["error", "ok"]
    assert rec.items[0].error_kind == "transient" and rec.items[1].attempt == 2
    assert rec.items[1].request == P.messages
    assert rec.items[1].response_text == '{"x":1}'


def test_context_travels_to_record() -> None:
    rec = ListRecorder()
    p = P.model_copy(update={"context": CallContext(run_id=3, email_ids=("a",))})
    gw(chats={"m1": FakeChat([Out(x=1)])}, recorder=rec).generate_structured(p, Out)
    assert rec.items[0].context == CallContext(run_id=3, email_ids=("a",))


def test_fallback_rotates_models_on_rate_limit() -> None:
    g = gw(
        models=["m1", "m2"],
        chats={
            "m1": FakeChat([RuntimeError("429 RESOURCE_EXHAUSTED")]),
            "m2": FakeChat([Out(x=2)]),
        },
    )
    assert g.generate_structured(P, Out).model == "m2"


def test_rate_limit_honors_retry_delay_from_error() -> None:
    sleeps: list[float] = []
    chat = FakeChat([RuntimeError("429 retry in 7s"), Out(x=1)])
    gw(chats={"m1": chat}, sleeps=sleeps).generate_structured(P, Out)
    assert 8 <= sleeps[0] <= 10  # delay + 1, plus up to 2s of jitter


def test_fatal_error_raises_immediately_and_is_recorded() -> None:
    rec = ListRecorder()
    chat = FakeChat([RuntimeError("401 API key not valid"), Out(x=1)])
    with pytest.raises(LLMFatalError):
        gw(chats={"m1": chat}, recorder=rec).generate_structured(P, Out)
    assert len(rec.items) == 1 and rec.items[0].error_kind == "fatal"


def test_bad_output_retries_without_sleep() -> None:
    sleeps: list[float] = []
    res = gw(chats={"m1": FakeChat([None, Out(x=1)])}, sleeps=sleeps).generate_structured(P, Out)
    assert res.parsed == Out(x=1) and sleeps == []


def test_exhausted_after_max_attempts() -> None:
    chat = FakeChat([RuntimeError("503"), RuntimeError("503"), RuntimeError("503")])
    with pytest.raises(LLMExhaustedError):
        gw(max_attempts=3, chats={"m1": chat}).generate_structured(P, Out)


def test_recorder_failure_does_not_break_call() -> None:
    g = gw(chats={"m1": FakeChat([Out(x=1)])}, recorder=BrokenRecorder())
    assert g.generate_structured(P, Out).parsed == Out(x=1)


def test_free_mode_paces_calls_by_rpm() -> None:
    clock = FakeClock()
    g = gw(mode="free", rpm=15, clock=clock, chats={"m1": FakeChat([Out(x=1), Out(x=2)])})
    g.generate_structured(P, Out)
    g.generate_structured(P, Out)
    assert clock.slept == [4.0]


def test_fast_mode_does_not_pace() -> None:
    clock = FakeClock()
    g = gw(mode="fast", clock=clock, chats={"m1": FakeChat([Out(x=1), Out(x=2)])})
    g.generate_structured(P, Out)
    g.generate_structured(P, Out)
    assert clock.slept == []


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("GEMINI_MODELS", "LLM_MODE", "FREE_TIER_RPM", "LLM_MAX_ATTEMPTS"):
        monkeypatch.delenv(k, raising=False)
    assert load_llm_config() == LLMConfig(
        models=("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"), mode="free", rpm=15,
        max_attempts=8,
    )


def test_load_config_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "turbo")
    with pytest.raises(ValidationError):
        load_llm_config()


USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


@tool
def get_email_body(email_id: str) -> str:
    """Body of one email.

    Args:
        email_id: id
    """
    return email_id


def ai_turn(content: str = "", calls: int = 0) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[
            {"id": f"c{i + 1}", "name": "get_email_body", "args": {"email_id": "a"}}
            for i in range(calls)
        ],
        usage_metadata=USAGE,
    )


def test_generate_turn_returns_tool_calls() -> None:
    chat = FakeChat([], [ai_turn(calls=1)])
    turn = gw(chats={"m1": chat}).generate_turn(P, [get_email_body])
    assert chat.bound == ["get_email_body"]
    assert turn.tool_calls == (
        ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"}),
    )
    assert turn.content == "" and turn.model == "m1"
    assert turn.usage.total_tokens == 15


def test_generate_turn_retries_transient_and_records_both() -> None:
    rec = ListRecorder()
    chat = FakeChat([], [RuntimeError("503 UNAVAILABLE"), ai_turn("pronto")])
    turn = gw(chats={"m1": chat}, recorder=rec).generate_turn(P, [get_email_body])
    assert turn.content == "pronto" and turn.tool_calls == ()
    assert [r.status for r in rec.items] == ["error", "ok"]
    assert rec.items[1].response_text == "pronto"


def test_generate_turn_records_tool_calls_as_text() -> None:
    rec = ListRecorder()
    gw(chats={"m1": FakeChat([], [ai_turn(calls=1)])}, recorder=rec).generate_turn(
        P, [get_email_body]
    )
    assert rec.items[0].response_text == 'get_email_body({"email_id":"a"})'


def test_tool_messages_round_trip_to_langchain() -> None:
    call = ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"})
    prompt = Prompt(
        purpose="decide",
        messages=(
            ChatMessage(role="human", content="oi"),
            ChatMessage(role="ai", content="", tool_calls=(call,)),
            ChatMessage(role="tool", content="corpo", tool_call_id="c1"),
        ),
    )
    chat = FakeChat([], [ai_turn("ok")])
    gw(chats={"m1": chat}).generate_turn(prompt, [get_email_body])
    _, ai, tool_msg = chat.tool_runnable.seen[0]
    assert isinstance(ai, AIMessage) and ai.tool_calls[0]["id"] == "c1"
    assert ai.tool_calls[0]["args"] == {"email_id": "a"}
    assert isinstance(tool_msg, ToolMessage) and tool_msg.tool_call_id == "c1"
    assert tool_msg.content == "corpo"


def test_generate_turn_retries_non_ai_output_without_sleep() -> None:
    rec, sleeps = ListRecorder(), list[float]()
    chat = FakeChat([], ["garbage", ai_turn("ok")])
    turn = gw(chats={"m1": chat}, recorder=rec, sleeps=sleeps).generate_turn(P, [get_email_body])
    assert turn.content == "ok" and sleeps == []
    assert rec.items[0].error_kind == "bad_output"


@pytest.mark.parametrize(
    "kw",
    [
        {"role": "human", "tool_calls": (ToolCallRequest(id="c", name="n", args={}),)},
        {"role": "ai", "tool_call_id": "c"},
        {"role": "tool"},
    ],
)
def test_chat_message_rejects_invalid_tool_fields(kw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"content": "x", **kw})


def test_agent_ids_travel_in_call_context() -> None:
    rec = ListRecorder()
    p = P.model_copy(update={"context": CallContext(agent_run_id=1, agent_step_id=2)})
    gw(chats={"m1": FakeChat([], [ai_turn("ok")])}, recorder=rec).generate_turn(p, [get_email_body])
    assert (rec.items[0].context.agent_run_id, rec.items[0].context.agent_step_id) == (1, 2)


SIG_KEY = "__gemini_function_call_thought_signatures__"


def test_generate_turn_keeps_thought_signatures() -> None:
    msg = ai_turn(calls=1)
    msg.additional_kwargs[SIG_KEY] = {"c1": b"sig-bytes"}
    turn = gw(chats={"m1": FakeChat([], [msg])}).generate_turn(P, [get_email_body])
    assert turn.signatures == {"c1": "c2lnLWJ5dGVz"}  # base64 of b"sig-bytes"


def test_thought_signatures_round_trip_to_langchain() -> None:
    call = ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"})
    prompt = Prompt(
        purpose="decide",
        messages=(
            ChatMessage(role="human", content="oi"),
            ChatMessage(role="ai", content="", tool_calls=(call,), signatures={"c1": "c2ln"}),
            ChatMessage(role="tool", content="corpo", tool_call_id="c1"),
        ),
    )
    chat = FakeChat([], [ai_turn("ok")])
    gw(chats={"m1": chat}).generate_turn(prompt, [get_email_body])
    ai = chat.tool_runnable.seen[0][1]
    assert ai.additional_kwargs[SIG_KEY] == {"c1": "c2ln"}


def test_signatures_only_on_ai_messages() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="human", content="x", signatures={"c1": "s"})
