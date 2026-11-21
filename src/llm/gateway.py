"""The single entry point for LLM calls.

Every agent and tool goes through `LLMGateway`: it paces calls in free mode,
retries with backoff, rotates models on failure, validates structured output
and records every attempt (JSON log line + optional recorder).

One instance per process: pacing state lives in the instance.
"""
import base64
import math
import random
import re
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from google import genai
from google.genai import types as genai_types
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, ValidationError

from src.db.vector import EMBED_DIM
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.types import (
    AssistantTurn,
    BinaryPart,
    CallContext,
    CallRecord,
    CallRecorder,
    Check,
    ChatMessage,
    ChatModelLike,
    EmbedderLike,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    ErrorKind,
    Prompt,
    StructuredResult,
    TokenUsage,
    ToolCallRequest,
    render_turn,
)
from src.utils.console import log

# Where langchain-google-genai keeps Gemini 3 function-call thought signatures.
SIGNATURES_KEY = "__gemini_function_call_thought_signatures__"
# Gemini's documented stand-in when a function call's signature came from
# another model: "skip_thought_signature_validator" is its urlsafe-base64
# spelling; LangChain decodes standard base64, so send the same bytes that way.
NEUTRAL_SIGNATURE = "skip/thought/signature/validator"
# After a fallback answers, later calls start there for a while instead of
# knocking on the overloaded models above it first.
STICKY_S = 600
# A model that just timed out / 503'd sits out this long (unless every model
# of the chain is sitting out).
COOLDOWN_S = 300
# Daily quotas reset at midnight Pacific time.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
BACKOFF_BASE_S = 3
BACKOFF_MAX_S = 120
# A 429 that asks for a longer wait means the quota (usually the daily one)
# is gone: fail now and let the job queue retry later instead of blocking.
RATE_WAIT_MAX_S = 120
DEFAULT_EMBED_MODEL = "gemini-embedding-2"
ERROR_TEXT_MAX = 300


class _BadOutput(Exception):
    """The model answered but the output is not the requested shape."""

    def __init__(self, message: str, usage: TokenUsage, text: str | None) -> None:
        super().__init__(message)
        self.usage = usage
        self.text = text


class _Answer[R]:
    """One successful attempt: the value plus what goes to the call record."""

    __slots__ = ("value", "usage", "text")

    def __init__(self, value: R, usage: TokenUsage, text: str | None) -> None:
        self.value = value
        self.usage = usage
        self.text = text


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


class _QuotaGone(Exception):
    """A 429 for the daily quota, or one whose advised wait exceeds RATE_WAIT_MAX_S."""

    def __init__(self, message: str, until: datetime) -> None:
        super().__init__(message)
        self.until = until


def next_quota_reset(now: datetime) -> datetime:
    """The next midnight in America/Los_Angeles, as UTC."""
    local = now.astimezone(QUOTA_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), QUOTA_TZ)
    return midnight.astimezone(UTC)


def _file_block(part: BinaryPart) -> dict[str, str]:
    if part.mime_type.startswith("image/"):
        kind = "image"
    elif part.mime_type.startswith("audio/"):
        kind = "audio"
    else:
        kind = "file"
    return {
        "type": kind,
        "base64": base64.b64encode(part.data).decode("ascii"),
        "mime_type": part.mime_type,
    }


def _to_langchain(msg: ChatMessage, model: str | None = None) -> BaseMessage:
    if msg.role == "system":
        return SystemMessage(content=msg.content)
    if msg.role == "human":
        if msg.parts:
            blocks: list[str | dict[str, str]] = [{"type": "text", "text": msg.content}]
            blocks += [_file_block(p) for p in msg.parts]
            return HumanMessage(content=blocks)
        return HumanMessage(content=msg.content)
    if msg.role == "tool":
        return ToolMessage(content=msg.content, tool_call_id=msg.tool_call_id or "")
    sigs = dict(msg.signatures)
    if sigs and msg.signatures_model is not None and model is not None and msg.signatures_model != model:
        sigs = {k: NEUTRAL_SIGNATURE for k in sigs}
    return AIMessage(
        content=msg.content,
        tool_calls=[{"id": c.id, "name": c.name, "args": c.args} for c in msg.tool_calls],
        additional_kwargs={SIGNATURES_KEY: sigs} if sigs else {},
    )


def _signatures(out: AIMessage) -> dict[str, str]:
    raw = out.additional_kwargs.get(SIGNATURES_KEY)
    if not isinstance(raw, dict):
        return {}
    sigs: dict[str, str] = {}
    for call_id, sig in raw.items():
        if isinstance(sig, bytes):
            sigs[str(call_id)] = base64.b64encode(sig).decode("ascii")
        elif isinstance(sig, str):
            sigs[str(call_id)] = sig
    return sigs


def chat_client_factory(
    timeout_s: float, api_key: str | None = None
) -> Callable[[str], ChatModelLike]:
    """`api_key` None = GOOGLE_API_KEY from the environment."""

    def build(model: str) -> ChatModelLike:
        if api_key:
            return ChatGoogleGenerativeAI(
                model=model, max_retries=0, timeout=timeout_s, google_api_key=api_key
            )
        return ChatGoogleGenerativeAI(model=model, max_retries=0, timeout=timeout_s)

    return build




class GenAIEmbedder:
    """google-genai embeddings (LangChain's class is text-only).

    One `Content` per input: a list of plain strings would be aggregated by
    gemini-embedding-2 into a single vector.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key  # None = GOOGLE_API_KEY from the environment
        self._client: genai.Client | None = None

    def embed(
        self, model: str, inputs: Sequence[EmbedInput], task: EmbedTask, dim: int
    ) -> tuple[tuple[float, ...], ...]:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key) if self._api_key else genai.Client()
        contents: list[genai_types.Content] = []
        for item in inputs:
            parts = [genai_types.Part.from_text(text=item.text)]
            if item.part is not None:
                parts.append(
                    genai_types.Part.from_bytes(data=item.part.data, mime_type=item.part.mime_type)
                )
            contents.append(genai_types.Content(role="user", parts=parts))
        out = self._client.models.embed_content(
            model=model,
            contents=contents,
            config=genai_types.EmbedContentConfig(output_dimensionality=dim, task_type=task),
        )
        return tuple(tuple(e.values or ()) for e in out.embeddings or ())


def _normalized(v: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in v))
    return v if norm == 0 else tuple(x / norm for x in v)


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
        embed_model: str = DEFAULT_EMBED_MODEL,
        embedder: EmbedderLike | None = None,
        mode_source: Callable[[], Literal["free", "fast"]] | None = None,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not models:
            raise ValueError("at least one model is required")
        self.models = tuple(models)
        self.mode = mode
        self.min_interval_s = 60 / rpm
        self.max_attempts = max_attempts
        self.recorder = recorder
        self._chat_factory = chat_factory or chat_client_factory(90, api_key)
        self.embed_model = embed_model
        self.mode_source = mode_source  # runtime override (app_settings.llm_mode)
        self._embedder = embedder or GenAIEmbedder(api_key)
        # LangChain clients are not shared across threads (worker lanes).
        self._local = threading.local()
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last_call: dict[str, float] = {}
        self._wall = wall
        # model -> when its daily quota comes back (shared by every lane)
        self._exhausted: dict[str, datetime] = {}
        # the model that answered last and until when calls start with it
        self._sticky: tuple[str, datetime] | None = None
        # model -> until when it sits out after a transient failure
        self._cooling: dict[str, datetime] = {}

    def _available(self, models: Sequence[str]) -> tuple[str, ...]:
        now = self._wall()
        with self._lock:
            freed = [m for m, until in self._exhausted.items() if until <= now]
            for m in freed:
                del self._exhausted[m]
            if freed:  # a better model is back: start from the top again
                self._sticky = None
            for m in [m for m, until in self._cooling.items() if until <= now]:
                del self._cooling[m]
            ok = tuple(m for m in models if m not in self._exhausted)
            ok = tuple(m for m in ok if m not in self._cooling) or ok
            if self._sticky is not None:
                model, until = self._sticky
                if until <= now or model not in ok:
                    self._sticky = None
                else:
                    start = ok.index(model)
                    ok = ok[start:] + ok[:start]
            return ok

    def under_pressure(self) -> bool:
        """Some model of the chain is paused (503/timeout) or out of its daily
        quota: the backfill's turbo leaves this key to the app."""
        self._available(self.models)  # drops expired pauses/quotas
        with self._lock:
            return any(m in self._cooling or m in self._exhausted for m in self.models) or (
                self.embed_model in self._cooling or self.embed_model in self._exhausted
            )

    def _stick(self, model: str, chain: Sequence[str]) -> None:
        with self._lock:
            self._sticky = (
                None if model == chain[0]
                else (model, self._wall() + timedelta(seconds=STICKY_S))
            )

    def _mark_exhausted(self, model: str, until: datetime) -> None:
        with self._lock:
            self._exhausted[model] = until
        log(f"cota de {model} esgotada até {until.isoformat()}; seguindo sem ele")

    def _chat(self, model: str) -> ChatModelLike:
        chats: dict[str, ChatModelLike] | None = getattr(self._local, "chats", None)
        if chats is None:
            chats = self._local.chats = {}
        if model not in chats:
            chats[model] = self._chat_factory(model)
        return chats[model]

    def _pace(self, model: str) -> None:
        """Free mode: reserve the model's next slot under the lock, then wait.

        Free-tier limits are per model, so each model has its own spacing;
        several threads (worker lanes) share one gateway and book slots here.
        """
        mode = self.mode_source() if self.mode_source is not None else self.mode
        with self._lock:
            now = self._clock()
            last = self._last_call.get(model)
            if mode != "free" or last is None:
                self._last_call[model] = now
                return
            wait = max(0.0, self.min_interval_s - (now - last))
            self._last_call[model] = now + wait
        if wait > 0:
            self._sleep(wait)

    def _mark_done(self, model: str) -> None:
        with self._lock:
            now = self._clock()
            last = self._last_call.get(model)
            self._last_call[model] = now if last is None else max(last, now)

    def _backoff(
        self, kind: ErrorKind, exc: BaseException, attempt: int, model: str, switching: bool
    ) -> None:
        """Wait before the next attempt; `switching` = it goes to another model,
        which has its own limits, so only the quota check applies."""
        exp = min(BACKOFF_BASE_S * 2 ** attempt, BACKOFF_MAX_S)
        delay = _retry_delay(exc) if kind == "rate" else None
        if kind == "rate":
            if "PerDay" in str(exc):
                raise _QuotaGone(f"cota diária esgotada em {model}",
                                 next_quota_reset(self._wall())) from exc
            if delay is not None and delay > RATE_WAIT_MAX_S:
                raise _QuotaGone(f"cota esgotada em {model} (pede {delay:.0f}s)",
                                 self._wall() + timedelta(seconds=delay)) from exc
        if switching:
            return
        if kind == "rate":
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

    def _attempts[R](
        self,
        prompt: Prompt,
        call: Callable[[str], _Answer[R]],
        models: Sequence[str] | None = None,
    ) -> tuple[R, str, TokenUsage]:
        """Pace, call, classify errors, back off and record every attempt.

        `call` gets the attempt's model name; `prompt` is what gets recorded.
        """
        chain = tuple(models or prompt.models or self.models)
        last_exc: BaseException | None = None
        kinds: list[ErrorKind] = []
        budget = min(prompt.attempts or self.max_attempts, self.max_attempts)
        cycle: set[str] = set()  # models tried since the last back-off

        for attempt in range(budget):
            rotation = self._available(chain)
            if not rotation:
                raise LLMExhaustedError(
                    f"cota do dia esgotada em todos os modelos: {', '.join(chain)}",
                    kinds=("quota",),
                ) from last_exc
            fresh = [m for m in rotation if m not in cycle]
            if not fresh:  # every available model failed once: start over
                cycle.clear()
                fresh = list(rotation)
            model = fresh[0]
            cycle.add(model)
            self._pace(model)
            started = self._clock()
            try:
                answer = call(model)
            except (Exception, ValidationError) as exc:  # noqa: BLE001 - classified below
                self._mark_done(model)
                ended = self._clock()
                last_exc = exc
                kind = _error_kind(exc)
                kinds.append(kind)
                bad = exc if isinstance(exc, _BadOutput) else None
                self._record(
                    CallRecord(
                        purpose=prompt.purpose,
                        model=model,
                        attempt=attempt + 1,
                        status="error",
                        latency_ms=int((ended - started) * 1000),
                        usage=bad.usage if bad else TokenUsage(),
                        error_kind=kind,
                        error=f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_MAX],
                        request=prompt.messages,
                        response_text=bad.text if bad else None,
                        context=prompt.context,
                    )
                )
                with self._lock:
                    if self._sticky is not None and self._sticky[0] == model:
                        self._sticky = None  # it stopped answering: back to the top
                    cools = kind in ("rate", "transient") and len(chain) > 1
                    if cools:
                        self._cooling[model] = self._wall() + timedelta(seconds=COOLDOWN_S)
                if kind == "fatal":
                    raise LLMFatalError(f"erro não recuperável ({model}): {exc}") from exc
                # back off only when no untried model is left in this cycle
                switching = any(m not in cycle for m in self._available(chain))
                try:
                    self._backoff(kind, exc, attempt, model, switching)
                except _QuotaGone as gone:
                    self._mark_exhausted(model, gone.until)
                continue

            self._stick(model, chain)
            self._mark_done(model)
            ended = self._clock()
            self._record(
                CallRecord(
                    purpose=prompt.purpose,
                    model=model,
                    attempt=attempt + 1,
                    status="ok",
                    latency_ms=int((ended - started) * 1000),
                    usage=answer.usage,
                    error_kind=None,
                    error=None,
                    request=prompt.messages,
                    response_text=answer.text,
                    context=prompt.context,
                )
            )
            return answer.value, model, answer.usage

        raise LLMExhaustedError(
            f"esgotou {budget} tentativas: {last_exc}", kinds=tuple(kinds)
        ) from last_exc

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        messages = [_to_langchain(m) for m in prompt.messages]

        def call(model: str) -> _Answer[T]:
            runnable = self._chat(model).with_structured_output(schema, include_raw=True)
            out = _StructuredRaw.model_validate(runnable.invoke(messages))
            usage = TokenUsage.from_langchain(out.raw.usage_metadata)
            if out.parsing_error is not None or not isinstance(out.parsed, schema):
                raise _BadOutput(
                    str(out.parsing_error or "empty structured output"), usage, out.raw.text
                )
            problem = check(out.parsed) if check is not None else None
            if problem is not None:
                raise _BadOutput(f"validação: {problem}", usage, out.raw.text)
            return _Answer(out.parsed, usage, out.raw.text)

        parsed, model, usage = self._attempts(prompt, call)
        return StructuredResult[T](parsed=parsed, model=model, usage=usage)

    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn:
        """One tool-calling turn. The caller executes the tools and loops."""

        def call(model: str) -> _Answer[AssistantTurn]:
            messages = [_to_langchain(m, model) for m in prompt.messages]
            out = self._chat(model).bind_tools(tools).invoke(messages)
            if not isinstance(out, AIMessage):
                raise _BadOutput(
                    f"esperava AIMessage, veio {type(out).__name__}", TokenUsage(), None
                )
            usage = TokenUsage.from_langchain(out.usage_metadata)
            try:
                calls = tuple(
                    ToolCallRequest(id=c["id"] or f"call-{i}", name=c["name"], args=c["args"])
                    for i, c in enumerate(out.tool_calls)
                )
            except ValidationError as exc:
                raise _BadOutput(str(exc), usage, out.text) from exc
            turn = AssistantTurn(
                content=out.text,
                tool_calls=calls,
                model="",
                usage=usage,
                signatures=_signatures(out),
            )
            return _Answer(turn, usage, render_turn(out.text, calls))

        turn, model, _ = self._attempts(prompt, call)
        return turn.model_copy(update={"model": model})

    def embed(
        self,
        inputs: Sequence[EmbedInput],
        *,
        purpose: str,
        task: EmbedTask,
        context: CallContext = CallContext(),
    ) -> EmbedResult:
        """One L2-normalized vector per input, with the same retries/records."""
        items = tuple(inputs)
        if not items:
            return EmbedResult(vectors=(), model=self.embed_model)
        files = tuple(i.part for i in items if i.part is not None)
        prompt = Prompt(
            purpose=purpose,
            messages=(
                ChatMessage(role="human", content=f"embed {len(items)} item(s)", parts=files),
            ),
            context=context,
        )

        def call(model: str) -> _Answer[tuple[tuple[float, ...], ...]]:
            vectors = self._embedder.embed(model, items, task, EMBED_DIM)
            if len(vectors) != len(items) or any(len(v) != EMBED_DIM for v in vectors):
                raise _BadOutput(
                    f"{len(vectors)} vetor(es) para {len(items)} entrada(s)", TokenUsage(), None
                )
            return _Answer(tuple(_normalized(v) for v in vectors), TokenUsage(), None)

        vectors, model, _ = self._attempts(prompt, call, models=(self.embed_model,))
        return EmbedResult(vectors=vectors, model=model)
