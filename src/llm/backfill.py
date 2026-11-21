"""The history backfill's LLM: its own API key, plus the app's in turbo.

App and new mail come first. The backfill always uses its own gateway; with
turbo on it alternates calls with the app's gateway (the same object the app
uses, so quota memory is shared), but skips it whenever that gateway is under
pressure (a model paused after 503/timeout, or out of its daily quota). A
daily-quota failure on one key moves the call to the other when allowed.
"""
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel

from src.llm.errors import LLMExhaustedError, quota_exhausted
from src.llm.types import (
    CallContext,
    Check,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    Prompt,
    StructuredResult,
)


class KeyedGateway(Protocol):
    def under_pressure(self) -> bool: ...

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]: ...

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult: ...


class BackfillLLM:
    def __init__(
        self, backfill: KeyedGateway, app: KeyedGateway | None, turbo: Callable[[], bool]
    ) -> None:
        self.backfill = backfill
        self.app = app  # None: one key only, turbo has nothing to add
        self.turbo = turbo
        self._next_app = False  # alternation state in turbo

    def _order(self) -> list[KeyedGateway]:
        if self.app is None or not self.turbo():
            return [self.backfill]
        app_ok = not self.app.under_pressure()
        if not app_ok:
            return [self.backfill]
        first_app, self._next_app = self._next_app, not self._next_app
        return [self.app, self.backfill] if first_app else [self.backfill, self.app]

    def _run[R](self, call: Callable[[KeyedGateway], R]) -> R:
        order = self._order()
        last: LLMExhaustedError | None = None
        for i, gw in enumerate(order):
            if gw is self.app and i > 0 and self.app.under_pressure():
                break  # the app key got busy meanwhile: leave it to the app
            try:
                return call(gw)
            except LLMExhaustedError as exc:
                if not quota_exhausted(exc) or i == len(order) - 1:
                    raise
                last = exc
        assert last is not None
        raise last

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        return self._run(lambda gw: gw.generate_structured(prompt, schema, check))

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        return self._run(lambda gw: gw.embed(inputs, purpose=purpose, task=task, context=context))
