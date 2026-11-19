"""The single entry point for LLM calls.

Every agent and tool goes through `LLMGateway`: it paces calls in free mode,
retries with backoff, rotates models on failure, validates structured output
and records every attempt (JSON log line + optional recorder).

One instance per process: pacing state lives in the instance.
"""
import random
import re
import time
from collections.abc import Callable, Sequence
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, ValidationError

from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.types import (
    CallRecord,
    CallRecorder,
    ChatMessage,
    ChatModelLike,
    ErrorKind,
    Prompt,
    StructuredResult,
    TokenUsage,
)
from src.utils.console import log

BACKOFF_BASE_S = 3
BACKOFF_MAX_S = 120
ERROR_TEXT_MAX = 300


class _BadOutput(Exception):
    """The model answered but the output is not the requested schema."""


class _StructuredRaw(BaseModel):
    """Shape of LangChain's `include_raw=True` output, validated on arrival."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    raw: AIMessage
    parsed: BaseModel | None
    parsing_error: BaseException | None


def _error_kind(exc: BaseException) -> ErrorKind:
    if isinstance(exc, _BadOutput):
        return "bad_output"
    text = f"{type(exc).__name__} {exc}"
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "rate"
    if any(
        s in text
        for s in (
            "500", "502", "503", "504", "UNAVAILABLE", "INTERNAL",
            "DEADLINE", "imeout", "Connect", "Connection", "Remote",
        )
    ):
        return "transient"
    if any(s in text for s in ("ValidationError", "OutputParser", "JSONDecode")):
        return "bad_output"
    return "fatal"


def _retry_delay(exc: BaseException) -> float | None:
    text = str(exc)
    for pattern in (
        r"retry in ([\d.]+)\s*s",
        r"retryDelay['\"]?:\s*['\"]([\d.]+)s",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return float(m.group(1))
    return None


def _to_langchain(msg: ChatMessage) -> BaseMessage:
    if msg.role == "system":
        return SystemMessage(content=msg.content)
    if msg.role == "human":
        return HumanMessage(content=msg.content)
    return AIMessage(content=msg.content)


def _default_chat(model: str) -> ChatModelLike:
    return ChatGoogleGenerativeAI(model=model, max_retries=0, timeout=90)


class LLMGateway:
    def __init__(
        self,
        models: Sequence[str],
        *,
        mode: Literal["free", "fast"] = "free",
        rpm: int = 15,
        max_attempts: int = 8,
        recorder: CallRecorder | None = None,
        chat_factory: Callable[[str], ChatModelLike] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not models:
            raise ValueError("at least one model is required")
        self.models = tuple(models)
        self.mode = mode
        self.min_interval_s = 60 / rpm
        self.max_attempts = max_attempts
        self.recorder = recorder
        self._chat_factory = chat_factory or _default_chat
        self._chats: dict[str, ChatModelLike] = {}
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def _chat(self, model: str) -> ChatModelLike:
        if model not in self._chats:
            self._chats[model] = self._chat_factory(model)
        return self._chats[model]

    def _pace(self) -> None:
        if self.mode != "free" or self._last_call is None:
            return
        wait = self.min_interval_s - (self._clock() - self._last_call)
        if wait > 0:
            self._sleep(wait)

    def _backoff(self, kind: ErrorKind, exc: BaseException, attempt: int, model: str) -> None:
        exp = min(BACKOFF_BASE_S * 2 ** attempt, BACKOFF_MAX_S)
        if kind == "rate":
            delay = _retry_delay(exc)
            seconds = (delay if delay is not None else exp) + 1
            why = f"429 em {model}"
        elif kind == "transient":
            seconds, why = exp, f"{str(exc)[:60]!r} em {model}"
        else:  # bad_output: retry right away
            log(f"saída inválida de {model}: {str(exc)[:80]!r}; tentando de novo")
            return
        seconds = max(1.0, seconds) + random.uniform(0, 2)
        log(f"{why}; aguardando {seconds:.0f}s")
        self._sleep(seconds)

    def _record(self, rec: CallRecord) -> None:
        log(rec.model_dump_json(exclude={"request", "response_text"}))
        if self.recorder is None:
            return
        try:
            self.recorder.record(rec)
        except Exception as exc:  # noqa: BLE001 - recording never breaks a call
            log(f"falha ao registrar chamada LLM: {type(exc).__name__}: {exc}")

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T]
    ) -> StructuredResult[T]:
        messages = [_to_langchain(m) for m in prompt.messages]
        last_exc: BaseException | None = None

        for attempt in range(self.max_attempts):
            model = self.models[attempt % len(self.models)]
            self._pace()
            started = self._clock()
            usage = TokenUsage()
            response_text: str | None = None
            try:
                runnable = self._chat(model).with_structured_output(schema, include_raw=True)
                out = _StructuredRaw.model_validate(runnable.invoke(messages))
                usage = TokenUsage.from_langchain(out.raw.usage_metadata)
                response_text = out.raw.text
                if out.parsing_error is not None or not isinstance(out.parsed, schema):
                    raise _BadOutput(str(out.parsing_error or "empty structured output"))
                parsed: T = out.parsed
            except (Exception, ValidationError) as exc:  # noqa: BLE001 - classified below
                self._last_call = self._clock()
                last_exc = exc
                kind = _error_kind(exc)
                self._record(
                    CallRecord(
                        purpose=prompt.purpose,
                        model=model,
                        attempt=attempt + 1,
                        status="error",
                        latency_ms=int((self._last_call - started) * 1000),
                        usage=usage,
                        error_kind=kind,
                        error=f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_MAX],
                        request=prompt.messages,
                        response_text=response_text,
                        context=prompt.context,
                    )
                )
                if kind == "fatal":
                    raise LLMFatalError(f"erro não recuperável ({model}): {exc}") from exc
                self._backoff(kind, exc, attempt, model)
                continue

            self._last_call = self._clock()
            self._record(
                CallRecord(
                    purpose=prompt.purpose,
                    model=model,
                    attempt=attempt + 1,
                    status="ok",
                    latency_ms=int((self._last_call - started) * 1000),
                    usage=usage,
                    error_kind=None,
                    error=None,
                    request=prompt.messages,
                    response_text=response_text,
                    context=prompt.context,
                )
            )
            return StructuredResult[T](parsed=parsed, model=model, usage=usage)

        raise LLMExhaustedError(
            f"esgotou {self.max_attempts} tentativas: {last_exc}"
        ) from last_exc
