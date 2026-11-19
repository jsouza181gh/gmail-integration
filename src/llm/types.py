"""Typed contracts of the LLM gateway."""
from typing import Literal, Protocol

from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from pydantic import BaseModel, ConfigDict

ErrorKind = Literal["rate", "transient", "bad_output", "fatal"]


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChatMessage(Schema):
    role: Literal["system", "human", "ai"]
    content: str


class CallContext(Schema):
    """What a call is about; travels to the call record."""

    run_id: int | None = None
    email_ids: tuple[str, ...] = ()


class Prompt(Schema):
    purpose: str
    messages: tuple[ChatMessage, ...]
    context: CallContext = CallContext()


class TokenUsage(Schema):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    audio_input_tokens: int = 0

    @classmethod
    def from_langchain(cls, usage: UsageMetadata | None) -> "TokenUsage":
        if usage is None:
            return cls()
        inp = usage.get("input_token_details", {})
        out = usage.get("output_token_details", {})
        return cls(
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            cached_input_tokens=inp.get("cache_read", 0),
            reasoning_tokens=out.get("reasoning", 0),
            audio_input_tokens=inp.get("audio", 0),
        )


class StructuredResult[T: BaseModel](Schema):
    parsed: T
    model: str
    usage: TokenUsage


class CallRecord(Schema):
    """One attempt against one model."""

    purpose: str
    model: str
    attempt: int
    status: Literal["ok", "error"]
    latency_ms: int
    usage: TokenUsage
    error_kind: ErrorKind | None
    error: str | None
    request: tuple[ChatMessage, ...]
    response_text: str | None
    context: CallContext


class CallRecorder(Protocol):
    def record(self, rec: CallRecord) -> None: ...


class StructuredGenerator(Protocol):
    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T]
    ) -> StructuredResult[T]: ...


class StructuredRunnable(Protocol):
    """What `chat.with_structured_output(..., include_raw=True)` returns.

    The output is untrusted and validated by the gateway.
    """

    def invoke(self, input: list[BaseMessage], /) -> object: ...


class ChatModelLike(Protocol):
    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool
    ) -> StructuredRunnable: ...
