"""Typed fakes for the agent ports and a context builder for tests."""
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.agent.context import AgentContext, LLMDeps, RunScope
from src.agent.types import (
    ActionKind,
    LabelInfo,
    MailPage,
    MailQuery,
    PolicyDecision,
    PolicyTarget,
    ProposalRef,
    RuleRef,
    SenderStats,
    Trigger,
)
from src.classification.taxonomy import Taxonomy
from src.db.rows import EmailRow
from src.llm.types import AssistantTurn, Prompt, StructuredResult, TokenUsage, ToolCallRequest


def make_email(email_id: str = "a", **over: object) -> EmailRow:
    base = EmailRow(
        id=email_id,
        sender="Loja <ofertas@loja.com>",
        subject="50% off",
        date="Thu, 19 Nov 2026 10:00:00 +0000",
        snippet="Só hoje",
        category=None,
        reason=None,
        attempts=0,
        last_error=None,
        received_at=None,
        is_relevant=None,
        deadline=None,
        has_document=None,
        ephemeral=None,
        category_set_id=None,
        updated_at=datetime(2026, 11, 19, tzinfo=UTC),
    )
    return base.model_copy(update=over)


class NoStructured:
    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T]
    ) -> StructuredResult[T]:
        raise AssertionError("structured LLM call not expected")


class FakeToolCaller:
    def __init__(self, turns: Sequence[AssistantTurn | Exception]) -> None:
        self.turns = list(turns)
        self.prompts: list[Prompt] = []
        self.offered: list[list[str]] = []

    def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn:
        self.prompts.append(prompt)
        self.offered.append([t.name for t in tools])
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def turn(content: str = "", *calls: ToolCallRequest) -> AssistantTurn:
    return AssistantTurn(content=content, tool_calls=calls, model="m1", usage=TokenUsage())


class NoTaxonomy:
    def active_taxonomy(self) -> Taxonomy | None:
        return None

    def active_preferences(self, scope: str) -> list[str]:
        return []


class NoMail:
    def list_messages(self, query: MailQuery) -> MailPage:
        raise AssertionError("mail not expected")

    def get_body(self, email_id: str) -> str:
        raise AssertionError("mail not expected")

    def list_labels(self) -> tuple[LabelInfo, ...]:
        raise AssertionError("mail not expected")


class NoLabels:
    def apply(self, email_id: str, names: Sequence[str]) -> tuple[str, ...]:
        raise AssertionError("labels not expected")


class NoHistory:
    def stats(self, address: str) -> SenderStats:
        raise AssertionError("history not expected")


class NoActions:
    def propose(
        self,
        email_id: str,
        kind: ActionKind,
        reason: str,
        *,
        run_id: int,
        trigger: Trigger,
        rule: RuleRef | None = None,
    ) -> ProposalRef:
        raise AssertionError("actions not expected")


class SuggestAll:
    def resolve(
        self,
        target: PolicyTarget,
        trigger: Trigger,
        *,
        rule_mode: Literal["suggest", "auto"] | None = None,
    ) -> PolicyDecision:
        return "suggest"


class NoKb:
    def entries(self, scopes: Sequence[str]) -> tuple[str, ...]:
        return ()


def make_ctx(
    *,
    run_id: int,
    config_id: int = 1,
    email_id: str | None = "a",
    trigger: Trigger = "webhook",
    caller: FakeToolCaller | None = None,
    extra: Mapping[str, object] | None = None,
    **deps: object,
) -> AgentContext:
    fields: dict[str, object] = {
        "run": RunScope(run_id=run_id, trigger=trigger, config_id=config_id),
        "email_id": email_id,
        "llm": LLMDeps(structured=NoStructured(), tools=caller or FakeToolCaller([])),
        "taxonomy": NoTaxonomy(),
        "mail": NoMail(),
        "labels": NoLabels(),
        "history": NoHistory(),
        "actions": NoActions(),
        "policy": SuggestAll(),
        "kb": NoKb(),
    }
    fields.update(extra or {})
    fields.update(deps)
    return AgentContext.model_validate(fields)
