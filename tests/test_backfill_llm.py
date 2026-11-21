from collections.abc import Sequence

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import RuntimeSettings
from src.llm.backfill import BackfillLLM
from src.llm.errors import LLMExhaustedError
from src.llm.types import (
    CallContext,
    ChatMessage,
    Check,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    Prompt,
    StructuredResult,
    TokenUsage,
)


class Out(BaseModel):
    x: int


P = Prompt(purpose="classify", messages=(ChatMessage(role="human", content="oi"),))


class FakeGateway:
    def __init__(self, name: str, *, pressure: bool = False, quota: bool = False) -> None:
        self.name = name
        self.pressure = pressure
        self.quota = quota
        self.calls = 0

    def under_pressure(self) -> bool:
        return self.pressure

    def _call(self) -> None:
        self.calls += 1
        if self.quota:
            raise LLMExhaustedError("cota do dia esgotada", kinds=("quota",))

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        self._call()
        return StructuredResult[T](parsed=schema.model_validate({"x": 1}), model=self.name,
                                   usage=TokenUsage())

    def embed(
        self, inputs: Sequence[EmbedInput], *, purpose: str, task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        self._call()
        return EmbedResult(vectors=((1.0,),), model=self.name)


def models(llm: BackfillLLM, n: int = 3) -> list[str]:
    return [llm.generate_structured(P, Out).model for _ in range(n)]


def test_turbo_off_uses_only_the_backfill_key() -> None:
    bf, app = FakeGateway("bf"), FakeGateway("app")
    assert models(BackfillLLM(bf, app, lambda: False)) == ["bf", "bf", "bf"]
    assert app.calls == 0


def test_turbo_on_alternates_between_keys() -> None:
    bf, app = FakeGateway("bf"), FakeGateway("app")
    assert models(BackfillLLM(bf, app, lambda: True)) == ["bf", "app", "bf"]
    llm = BackfillLLM(bf, app, lambda: True)
    assert llm.embed([EmbedInput(text="a")], purpose="embed_email", task="RETRIEVAL_DOCUMENT").model == "bf"


def test_turbo_never_touches_an_app_key_under_pressure() -> None:
    bf, app = FakeGateway("bf"), FakeGateway("app", pressure=True)
    assert models(BackfillLLM(bf, app, lambda: True)) == ["bf", "bf", "bf"]
    assert app.calls == 0


def test_turbo_moves_to_the_app_key_when_backfill_quota_is_gone() -> None:
    bf, app = FakeGateway("bf", quota=True), FakeGateway("app")
    assert models(BackfillLLM(bf, app, lambda: True), 2) == ["app", "app"]


def test_both_keys_out_of_quota_raises_quota() -> None:
    bf, app = FakeGateway("bf", quota=True), FakeGateway("app", quota=True)
    with pytest.raises(LLMExhaustedError) as e:
        BackfillLLM(bf, app, lambda: True).generate_structured(P, Out)
    assert e.value.kinds == ("quota",)


def test_single_key_ignores_turbo() -> None:
    bf = FakeGateway("bf")
    assert models(BackfillLLM(bf, None, lambda: True)) == ["bf", "bf", "bf"]


def test_turbo_setting_roundtrip(session_factory: sessionmaker[Session]) -> None:
    s = RuntimeSettings(session_factory)
    assert s.backfill_turbo() is False
    s.set_backfill_turbo(True)
    assert s.backfill_turbo() is True
    s.set_backfill_turbo(False)
    assert s.backfill_turbo() is False
