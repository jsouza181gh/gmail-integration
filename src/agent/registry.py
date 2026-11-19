"""Tool registry: each tool paired with the slices of context and state it may see.

A tool declares `runtime: ToolRuntime[C, S]` with small schemas `C`/`S`;
`AgentTool` holds the projections from the run's `AgentContext`/`AgentState`
onto them. The tool never sees anything outside its slices.
"""
from collections.abc import Callable, Sequence
from typing import Protocol

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.agent.context import AgentContext
from src.agent.state import AgentState
from src.agent.types import Trigger


def _no_stream(chunk: object) -> None:
    """Tools run outside a graph: nothing consumes streamed chunks."""


class ToolEntry(Protocol):
    """What the executor needs from a registered tool, whatever its slices."""

    @property
    def name(self) -> str: ...

    @property
    def tool(self) -> BaseTool: ...

    @property
    def contexts(self) -> frozenset[Trigger]: ...

    @property
    def planned_only(self) -> bool: ...

    def runtime(self, ctx: AgentContext, state: AgentState, call_id: str) -> object: ...


class AgentTool[C: BaseModel, S: BaseModel]:
    def __init__(
        self,
        tool: BaseTool,
        *,
        context_of: Callable[[AgentContext], C],
        state_of: Callable[[AgentState], S],
        contexts: frozenset[Trigger],
        planned_only: bool = False,
    ) -> None:
        self._tool = tool
        self._context_of = context_of
        self._state_of = state_of
        self._contexts = contexts
        self._planned_only = planned_only

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def tool(self) -> BaseTool:
        return self._tool

    @property
    def contexts(self) -> frozenset[Trigger]:
        return self._contexts

    @property
    def planned_only(self) -> bool:
        """Invoked by the flow itself, never offered to the model."""
        return self._planned_only

    def runtime(self, ctx: AgentContext, state: AgentState, call_id: str) -> ToolRuntime[C, S]:
        return ToolRuntime(
            state=self._state_of(state),
            context=self._context_of(ctx),
            config={},
            stream_writer=_no_stream,
            tool_call_id=call_id,
            store=None,
        )


class Registry:
    def __init__(self, tools: Sequence[ToolEntry]) -> None:
        self._tools = {t.name: t for t in tools}
        if len(self._tools) != len(tools):
            raise ValueError("nomes de tool duplicados")

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def for_model(self, trigger: Trigger) -> list[BaseTool]:
        return [
            t.tool
            for t in self._tools.values()
            if trigger in t.contexts and not t.planned_only
        ]
