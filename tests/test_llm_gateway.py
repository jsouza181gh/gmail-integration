from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ValidationError

from src.llm.config import LLMConfig, load_llm_config
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.gateway import LLMGateway
from src.llm.types import EmbedInput, EmbedTask
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


class Wall:
    """Wall clock for daily-quota memory; starts 2026-11-20 15:00 UTC."""

    def __init__(self) -> None:
        self.at = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


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
    wall: "Wall | None" = None,
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
        wall=(wall or Wall()).now,
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
    for k in ("GEMINI_MODELS", "LLM_MODE", "FREE_TIER_RPM", "LLM_MAX_ATTEMPTS", "EMBED_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert load_llm_config() == LLMConfig(
        models=("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"), mode="free", rpm=15,
        max_attempts=8, embed_model="gemini-embedding-2",
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


def test_semantic_check_failure_retries_on_next_model() -> None:
    rec, sleeps = ListRecorder(), list[float]()
    g = gw(
        models=["m1", "m2"],
        chats={"m1": FakeChat([Out(x=-1)]), "m2": FakeChat([Out(x=2)])},
        recorder=rec,
        sleeps=sleeps,
    )
    res = g.generate_structured(P, Out, check=lambda o: "x negativo" if o.x < 0 else None)
    assert res.parsed == Out(x=2) and res.model == "m2" and sleeps == []
    assert rec.items[0].error_kind == "bad_output" and "x negativo" in (rec.items[0].error or "")


# --- embeddings, binary parts, quota fail-fast ---------------------------------

class FakeEmbedder:
    def __init__(self, script: list[int | Exception]) -> None:
        self.script = script  # number of vectors to return, or an error
        self.calls: list[tuple[str, int, str, int]] = []

    def embed(self, model: str, inputs: Sequence["EmbedInput"], task: "EmbedTask", dim: int
              ) -> tuple[tuple[float, ...], ...]:
        self.calls.append((model, len(inputs), task, dim))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return tuple(tuple(3.0 if j == 0 else 4.0 if j == 1 else 0.0 for j in range(dim))
                     for _ in range(step))


def egw(embedder: FakeEmbedder, rec: ListRecorder | None = None,
        sleeps: list[float] | None = None) -> LLMGateway:
    return LLMGateway(("m1",), mode="fast", recorder=rec or ListRecorder(),
                      chat_factory=lambda m: FakeChat([]), embedder=embedder,
                      sleep=(sleeps if sleeps is not None else []).append, clock=lambda: 0.0)


def test_embed_sends_one_content_per_input() -> None:
    from src.db.vector import EMBED_DIM
    from src.llm.types import EmbedInput

    fake = FakeEmbedder([3])
    rec = ListRecorder()
    out = egw(fake, rec).embed([EmbedInput(text=t) for t in "abc"], purpose="embed_email",
                                task="RETRIEVAL_DOCUMENT")
    assert fake.calls == [("gemini-embedding-2", 3, "RETRIEVAL_DOCUMENT", EMBED_DIM)]
    assert len(out.vectors) == 3 and out.model == "gemini-embedding-2"
    assert abs(sum(v * v for v in out.vectors[0]) - 1.0) < 1e-9  # L2-normalized
    assert out.vectors[0][:2] == (0.6, 0.8)
    assert rec.items[0].purpose == "embed_email" and rec.items[0].status == "ok"


def test_embed_wrong_count_is_bad_output_and_retries() -> None:
    from src.llm.types import EmbedInput

    fake = FakeEmbedder([1, 2])
    out = egw(fake).embed([EmbedInput(text="a"), EmbedInput(text="b")], purpose="p",
                          task="RETRIEVAL_DOCUMENT")
    assert len(out.vectors) == 2 and len(fake.calls) == 2


def test_long_rate_limit_fails_fast() -> None:
    from src.llm.errors import quota_exhausted

    sleeps: list[float] = []
    chat = FakeChat([RuntimeError("429 RESOURCE_EXHAUSTED quota PerDay; retry in 3600s")])
    with pytest.raises(LLMExhaustedError) as e:
        gw(chats={"m1": chat}, sleeps=sleeps).generate_structured(P, Out)
    assert e.value.kinds == ("quota",) and quota_exhausted(e.value)
    assert sleeps == []


def test_exhausted_error_lists_kinds() -> None:
    from src.llm.errors import quota_exhausted

    chat = FakeChat([RuntimeError("503 UNAVAILABLE"), None])
    with pytest.raises(LLMExhaustedError) as e:
        gw(chats={"m1": chat}, max_attempts=2).generate_structured(P, Out)
    assert e.value.kinds == ("transient", "bad_output") and not quota_exhausted(e.value)


def test_human_parts_become_content_blocks() -> None:
    from langchain_core.messages import HumanMessage

    from src.llm.gateway import _to_langchain
    from src.llm.types import BinaryPart

    msg = ChatMessage(role="human", content="veja", parts=(
        BinaryPart(mime_type="application/pdf", data=b"%PDF"),
        BinaryPart(mime_type="image/png", data=b"\x89PNG"),
    ))
    lc = _to_langchain(msg)
    assert isinstance(lc, HumanMessage) and isinstance(lc.content, list)
    kinds = [b["type"] for b in lc.content if isinstance(b, dict)]
    assert kinds == ["text", "file", "image"]
    pdf = lc.content[1]
    assert isinstance(pdf, dict) and pdf["mime_type"] == "application/pdf"
    assert pdf["base64"] == "JVBERg=="


def test_parts_only_on_human() -> None:
    from src.llm.types import BinaryPart

    with pytest.raises(ValidationError):
        ChatMessage(role="system", content="x", parts=(BinaryPart(mime_type="image/png", data=b"x"),))


def test_mode_source_switches_pacing_at_runtime() -> None:
    from typing import Literal

    mode: list[Literal["free", "fast"]] = ["fast"]
    clock = FakeClock()
    g = LLMGateway(("m1",), mode="free", rpm=60, recorder=ListRecorder(),
                   chat_factory={"m1": FakeChat([Out(x=1), Out(x=2), Out(x=3)])}.__getitem__,
                   sleep=clock.sleep, clock=clock.now, mode_source=lambda: mode[0])
    g.generate_structured(P, Out)
    g.generate_structured(P, Out)
    assert clock.slept == []  # fast: no pacing
    mode[0] = "free"
    g.generate_structured(P, Out)
    assert clock.slept == [1.0]


def test_prompt_attempts_override() -> None:
    rec = ListRecorder()
    chat = FakeChat([RuntimeError("503")] * 5 + [Out(x=1)])
    p = P.model_copy(update={"attempts": 3})
    with pytest.raises(LLMExhaustedError):
        gw(max_attempts=8, chats={"m1": chat}, recorder=rec).generate_structured(p, Out)
    assert len(rec.items) == 3


def test_audio_part_becomes_audio_block() -> None:
    from src.llm.gateway import _to_langchain
    from src.llm.types import BinaryPart

    msg = ChatMessage(role="human", content="transcreva",
                      parts=(BinaryPart(mime_type="audio/wav", data=b"RIFF"),))
    lc = _to_langchain(msg)
    assert isinstance(lc.content, list)
    block = lc.content[1]
    assert isinstance(block, dict) and block["type"] == "audio"
    assert block["mime_type"] == "audio/wav" and block["base64"] == "UklGRg=="


def test_pace_reserves_slots_across_threads() -> None:
    import threading

    sleeps: list[float] = []
    g = LLMGateway(["m1"], mode="free", rpm=15, chat_factory=lambda m: FakeChat([]),
                   sleep=sleeps.append, clock=lambda: 0.0)
    for _ in range(3):
        t = threading.Thread(target=g._pace, args=("m1",))
        t.start()
        t.join()
    assert sleeps == [4.0, 8.0]


def test_chat_clients_are_per_thread() -> None:
    import threading

    made: list[str] = []

    def factory(model: str) -> FakeChat:
        made.append(model)
        return FakeChat([])

    g = LLMGateway(["m1"], chat_factory=factory)
    g._chat("m1")
    g._chat("m1")
    t = threading.Thread(target=lambda: g._chat("m1"))
    t.start()
    t.join()
    assert made == ["m1", "m1"]


DAILY = "429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel; retry in 30s"


def test_quota_gone_marks_model_and_moves_on() -> None:
    sleeps: list[float] = []
    chats = {"A": FakeChat([RuntimeError(DAILY)]), "B": FakeChat([Out(x=1), Out(x=2)])}
    g = gw(models=["A", "B"], chats=chats, sleeps=sleeps)
    assert g.generate_structured(P, Out).model == "B" and sleeps == []
    assert g.generate_structured(P, Out).model == "B"  # A is not called again today
    assert chats["A"].runnable.script == []


def test_exhausted_resets_at_pacific_midnight() -> None:
    wall = Wall()
    chats = {"A": FakeChat([RuntimeError(DAILY), Out(x=3)]), "B": FakeChat([Out(x=1), Out(x=2)])}
    g = gw(models=["A", "B"], chats=chats, wall=wall)
    g.generate_structured(P, Out)
    wall.at = datetime(2026, 11, 21, 7, 59, tzinfo=UTC)  # 23:59 PST
    assert g.generate_structured(P, Out).model == "B"
    wall.at = datetime(2026, 11, 21, 8, 1, tzinfo=UTC)  # 00:01 PST
    assert g.generate_structured(P, Out).model == "A"


def test_all_exhausted_fails_fast() -> None:
    sleeps: list[float] = []
    chats = {"A": FakeChat([RuntimeError(DAILY)]), "B": FakeChat([RuntimeError(DAILY)])}
    g = gw(models=["A", "B"], chats=chats, sleeps=sleeps)
    with pytest.raises(LLMExhaustedError) as e:
        g.generate_structured(P, Out)
    assert e.value.kinds == ("quota",) and sleeps == []
    with pytest.raises(LLMExhaustedError):
        g.generate_structured(P, Out)


def test_transient_switches_model_without_sleep() -> None:
    sleeps: list[float] = []
    chats = {"A": FakeChat([RuntimeError("503 UNAVAILABLE")]), "B": FakeChat([Out(x=1)])}
    assert gw(models=["A", "B"], chats=chats, sleeps=sleeps).generate_structured(P, Out).model == "B"
    assert sleeps == []


def test_transient_on_single_model_still_backs_off() -> None:
    sleeps: list[float] = []
    chat = FakeChat([RuntimeError("503 UNAVAILABLE"), Out(x=1)])
    gw(chats={"m1": chat}, sleeps=sleeps).generate_structured(P, Out)
    assert len(sleeps) == 1


def test_foreign_signatures_replaced() -> None:
    call = ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"})
    prompt = Prompt(
        purpose="chat",
        messages=(
            ChatMessage(role="human", content="oi"),
            ChatMessage(role="ai", content="", tool_calls=(call,), signatures={"c1": "c2ln"},
                        signatures_model="A"),
            ChatMessage(role="tool", content="corpo", tool_call_id="c1"),
        ),
    )
    chats = {"B": FakeChat([], [ai_turn("ok")])}
    gw(models=["B"], chats=chats).generate_turn(prompt, [get_email_body])
    ai = chats["B"].tool_runnable.seen[0][1]
    assert ai.additional_kwargs[SIG_KEY] == {"c1": "skip/thought/signature/validator"}


def test_own_signatures_kept() -> None:
    call = ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"})
    prompt = Prompt(purpose="chat", messages=(
        ChatMessage(role="human", content="oi"),
        ChatMessage(role="ai", content="", tool_calls=(call,), signatures={"c1": "c2ln"},
                    signatures_model="B"),
        ChatMessage(role="tool", content="corpo", tool_call_id="c1"),
    ))
    chats = {"B": FakeChat([], [ai_turn("ok")])}
    gw(models=["B"], chats=chats).generate_turn(prompt, [get_email_body])
    assert chats["B"].tool_runnable.seen[0][1].additional_kwargs[SIG_KEY] == {"c1": "c2ln"}


def test_chat_config_default_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm.config import load_chat_config

    monkeypatch.delenv("CHAT_MODELS", raising=False)
    assert load_chat_config().models == (
        "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    )


def test_full_cycle_of_failures_backs_off_once() -> None:
    sleeps: list[float] = []
    chats = {"A": FakeChat([RuntimeError("503 UNAVAILABLE"), Out(x=1)]),
             "B": FakeChat([RuntimeError("503 UNAVAILABLE")])}
    res = gw(models=["A", "B"], chats=chats, sleeps=sleeps).generate_structured(P, Out)
    assert res.model == "A" and len(sleeps) == 1  # A fails, B fails -> wait -> A


def test_neutral_signature_is_standard_base64() -> None:
    import base64

    from src.llm.gateway import NEUTRAL_SIGNATURE

    # LangChain decodes signatures as standard base64; Gemini documents the
    # urlsafe spelling "skip_thought_signature_validator" - same bytes.
    assert base64.b64decode(NEUTRAL_SIGNATURE, validate=True) == base64.urlsafe_b64decode(
        "skip_thought_signature_validator"
    )


def test_sticks_to_last_working_model() -> None:
    chats = {"A": FakeChat([RuntimeError("503 UNAVAILABLE")]), "B": FakeChat([Out(x=1), Out(x=2)])}
    g = gw(models=["A", "B"], chats=chats)
    assert g.generate_structured(P, Out).model == "B"
    assert g.generate_structured(P, Out).model == "B"  # A is not tried first again


def test_sticky_model_expires_after_ten_minutes() -> None:
    wall = Wall()
    chats = {"A": FakeChat([RuntimeError("503 UNAVAILABLE"), Out(x=3)]), "B": FakeChat([Out(x=1)])}
    g = gw(models=["A", "B"], chats=chats, wall=wall)
    g.generate_structured(P, Out)
    wall.at = wall.at + timedelta(minutes=11)
    assert g.generate_structured(P, Out).model == "A"


def test_chat_gateway_uses_short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm.config import CHAT_TIMEOUT_S, build_chat_gateway

    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    client = build_chat_gateway()._chat_factory("gemini-3.6-flash")
    assert CHAT_TIMEOUT_S == 15 and getattr(client, "timeout") == 15


def test_failed_model_cools_down_for_five_minutes() -> None:
    wall = Wall()
    chats = {
        "A": FakeChat([RuntimeError("503 UNAVAILABLE"), Out(x=9)]),
        "B": FakeChat([Out(x=1), RuntimeError("503 UNAVAILABLE")]),
        "C": FakeChat([Out(x=2)]),
    }
    g = gw(models=["A", "B", "C"], chats=chats, wall=wall)
    assert g.generate_structured(P, Out).model == "B"
    assert g.generate_structured(P, Out).model == "C"  # B fails; A still cooling
    wall.at = wall.at + timedelta(minutes=11)
    assert g.generate_structured(P, Out).model == "A"


def test_free_mode_paces_per_model() -> None:
    clock = FakeClock()
    chats = {"A": FakeChat([RuntimeError("503 UNAVAILABLE")]), "B": FakeChat([Out(x=1)])}
    gw(mode="free", rpm=15, clock=clock, models=["A", "B"], chats=chats).generate_structured(P, Out)
    assert clock.slept == []  # B has its own per-minute quota: no wait after A
