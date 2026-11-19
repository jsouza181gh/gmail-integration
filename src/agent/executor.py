"""Runs one tool call: availability check, sliced runtime, invoke, typed update.

Planned calls (made by a flow) and model calls go through the same path, so
both leave the same kind of step in the trail. Every failure becomes an
`erro: ...` tool message for the model; nothing here raises.
"""
import json
from typing import Literal

from langchain_core.messages import ToolCall, ToolMessage
from pydantic import TypeAdapter, ValidationError

from src.agent.context import AgentContext
from src.agent.registry import Registry
from src.agent.state import AgentState, StateUpdate
from src.agent.trail import Trail
from src.llm.types import ChatMessage, Schema, ToolCallRequest

ERROR_MAX = 500

_UPDATE: TypeAdapter[StateUpdate | None] = TypeAdapter(StateUpdate | None)


class ToolExecution(Schema):
    message: ChatMessage
    update: StateUpdate | None
    ok: bool


def _detail(call: ToolCallRequest, result: str) -> str:
    return json.dumps({"args": call.args, "result": result}, ensure_ascii=False)


class ToolExecutor:
    def __init__(self, registry: Registry, trail: Trail) -> None:
        self.registry = registry
        self.trail = trail

    def _fail(self, step_id: int, call: ToolCallRequest, reason: str) -> ToolExecution:
        content = f"erro: {reason}"[:ERROR_MAX]
        self.trail.close_step(step_id, "error", _detail(call, content))
        return ToolExecution(
            message=ChatMessage(role="tool", content=content, tool_call_id=call.id),
            update=None,
            ok=False,
        )

    def refuse(
        self,
        call: ToolCallRequest,
        ctx: AgentContext,
        reason: str,
        *,
        source: Literal["planned", "model"],
    ) -> ToolExecution:
        """Record a call the runtime chose not to run (e.g. a repeat)."""
        step_id = self.trail.open_step(ctx.run.run_id, "tool", call.name[:64], source)
        return self._fail(step_id, call, reason)

    def execute(
        self,
        call: ToolCallRequest,
        ctx: AgentContext,
        state: AgentState,
        *,
        source: Literal["planned", "model"],
    ) -> ToolExecution:
        step_id = self.trail.open_step(ctx.run.run_id, "tool", call.name[:64], source)
        entry = self.registry.get(call.name)
        if entry is None:
            return self._fail(step_id, call, f"a tool '{call.name}' não existe")
        allowed = entry.offered if source == "model" else entry.contexts
        if ctx.run.trigger not in allowed:
            return self._fail(step_id, call, f"a tool '{call.name}' não disponível aqui")

        try:
            runtime = entry.runtime(ctx.for_step(step_id), state, call.id)
            out = entry.tool.invoke(
                ToolCall(
                    name=call.name,
                    args={**call.args, "runtime": runtime},
                    id=call.id,
                    type="tool_call",
                )
            )
        except ValidationError as exc:
            return self._fail(step_id, call, f"argumentos inválidos: {exc.errors()[:3]}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the model and the trail
            return self._fail(step_id, call, f"{type(exc).__name__}: {exc}")

        if not isinstance(out, ToolMessage):
            return self._fail(step_id, call, f"resposta inesperada ({type(out).__name__})")
        try:
            update = _UPDATE.validate_python(out.artifact)
        except ValidationError as exc:
            return self._fail(step_id, call, f"atualização de estado inválida: {exc}")

        content = out.text
        self.trail.close_step(step_id, "ok", _detail(call, content))
        return ToolExecution(
            message=ChatMessage(role="tool", content=content, tool_call_id=call.id),
            update=update,
            ok=True,
        )
