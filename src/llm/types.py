"""Typed contracts of the LLM gateway."""
import json
from collections.abc import Sequence
from typing import Literal, Protocol, Self, runtime_checkable

from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

ErrorKind = Literal["rate", "transient", "bad_output", "fatal"]


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCallRequest(Schema):
    """One tool call the model asked for (args already parsed from JSON)."""

    id: str
    name: str
    args: dict[str, JsonValue]


class ChatMessage(Schema):
    role: Literal["system", "human", "ai", "tool"]
    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()  # role "ai" only
    tool_call_id: str | None = None  # role "tool" only

    @model_validator(mode="after")
    def _check_tool_fields(self) -> Self:
        if self.tool_calls and self.role != "ai":
            raise ValueError("tool_calls só em mensagens 'ai'")
        if (self.tool_call_id is not None) != (self.role == "tool"):
            raise ValueError("tool_call_id é obrigatório em 'tool' e proibido nos demais")
        return self


def render_turn(content: str, calls: Sequence[ToolCallRequest]) -> str:
    """Readable record of a turn: text plus `name(args)` per tool call."""
    parts = [content] if content else []
    parts += [
        f"{c.name}({json.dumps(c.args, separators=(',', ':'), ensure_ascii=False)})"
        for c in calls
    ]
    return "\n".join(parts)


class CallContext(Schema):
    """What a call is about; travels to the call record."""

    run_id: int | None = None
    email_ids: tuple[str, ...] = ()
    agent_run_id: int | None = None
    agent_step_id: int | None = None


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


class AssistantTurn(Schema):
    """One model turn of a tool-calling conversation."""

    content: str
    tool_calls: tuple[ToolCallRequest, ...]
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


@runtime_checkable
class StructuredGenerator(Protocol):
    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T]
    ) -> StructuredResult[T]: ...


@runtime_checkable
class ToolCaller(Protocol):
    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn: ...


class StructuredRunnable(Protocol):
    """What `chat.with_structured_output(..., include_raw=True)` returns.

    The output is untrusted and validated by the gateway.
    """

    def invoke(self, input: list[BaseMessage], /) -> object: ...


class ToolRunnable(Protocol):
    """What `chat.bind_tools(...)` returns; output validated as `AIMessage`."""

    def invoke(self, input: list[BaseMessage], /) -> object: ...


class ChatModelLike(Protocol):
    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool
    ) -> StructuredRunnable: ...

    def bind_tools(self, tools: Sequence[BaseTool]) -> ToolRunnable: ...
