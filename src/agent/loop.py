"""The agentic part of a run: model turn -> tool calls -> repeat, bounded.

Ends when the model answers without tool calls (its text is the run's
summary) or after `max_turns` model calls. LLM failures close the step and
propagate: the caller decides whether the run fails.
"""
import json
from collections.abc import Callable
from typing import Literal

from src.agent.context import AgentContext
from src.agent.executor import ToolExecutor
from src.agent.state import AgentState, reduce
from src.agent.trail import Trail
from src.llm.types import ChatMessage, Prompt, Schema, ToolCaller, ToolCallRequest, render_turn


class LoopResult(Schema):
    state: AgentState
    summary: str
    status: Literal["done", "truncated"]


def _signature(call: ToolCallRequest) -> str:
    return f"{call.name}:{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"


class AgentLoop:
    def __init__(self, executor: ToolExecutor, trail: Trail, caller: ToolCaller) -> None:
        self.executor = executor
        self.trail = trail
        self.caller = caller

    def run(
        self,
        ctx: AgentContext,
        state: AgentState,
        prompt: Prompt,
        *,
        max_turns: int,
        on_tool: Callable[[str], None] | None = None,
    ) -> LoopResult:
        """`on_tool` hears each tool name before it runs (chat progress)."""
        messages = list(prompt.messages)
        tools = self.executor.registry.for_model(ctx.run.trigger)
        seen: set[str] = set()

        for _ in range(max_turns):
            step_id = self.trail.open_step(ctx.run.run_id, "llm", prompt.purpose, "flow")
            request = prompt.model_copy(
                update={"messages": tuple(messages), "context": ctx.for_step(step_id).call()}
            )
            try:
                turn = self.caller.generate_turn(request, tools)
            except Exception as exc:
                self.trail.close_step(step_id, "error", f"{type(exc).__name__}: {exc}")
                raise
            self.trail.close_step(step_id, "ok", render_turn(turn.content, turn.tool_calls))
            state = state.model_copy(update={"turns_used": state.turns_used + 1})

            if not turn.tool_calls:
                return LoopResult(state=state, summary=turn.content.strip(), status="done")

            messages.append(
                ChatMessage(
                    role="ai",
                    content=turn.content,
                    tool_calls=turn.tool_calls,
                    signatures=turn.signatures,
                    signatures_model=turn.model or None,
                )
            )
            for call in turn.tool_calls:
                if on_tool is not None:
                    on_tool(call.name)
                sig = _signature(call)
                if sig in seen:
                    result = self.executor.refuse(
                        call, ctx, "chamada repetida com os mesmos argumentos; use o resultado anterior",
                        source="model",
                    )
                else:
                    seen.add(sig)
                    result = self.executor.execute(call, ctx, state, source="model")
                if result.update is not None:
                    state = reduce(state, result.update)
                messages.append(result.message)

        return LoopResult(state=state, summary="", status="truncated")
