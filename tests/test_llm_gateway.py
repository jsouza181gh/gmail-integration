from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError

from src.llm.config import LLMConfig, load_llm_config
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.gateway import LLMGateway
from src.llm.types import CallContext, CallRecord, ChatMessage, Prompt, TokenUsage


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


class FakeChat:
    def __init__(self, script: list[Step]):
        self.runnable = FakeRunnable(script)

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
        models=("gemini-3.5-flash-lite",), mode="free", rpm=15, max_attempts=8
    )


def test_load_config_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "turbo")
    with pytest.raises(ValidationError):
        load_llm_config()
