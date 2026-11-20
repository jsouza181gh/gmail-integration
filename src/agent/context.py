"""AgentContext: what a run may use, fixed for the run's lifetime.

It is the root tools are projected from: no tool ever receives it whole
(see `registry.AgentTool`). Dependencies are protocol-typed and validated
with `isinstance` on construction.
"""
from typing import Self

from pydantic import BaseModel, ConfigDict

from src.agent.ports import (
    ActionSink,
    CardSink,
    DocCatalog,
    EmailLookup,
    KbReader,
    Labeler,
    MailReader,
    PendingReader,
    PolicyReader,
    Retriever,
    RuleReader,
    SenderHistorySource,
    TaxonomySource,
)
from src.agent.types import ChatChannel, Trigger
from src.llm.types import CallContext, Schema, StructuredGenerator, ToolCaller


class Deps(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class RunScope(Schema):
    run_id: int
    trigger: Trigger
    config_id: int


class ChatScope(Schema):
    """The chat turn a run answers: where cards go and how the user spoke."""

    conversation_id: int
    message_id: int  # the assistant message being produced
    channel: ChatChannel


class LLMDeps(Deps):
    structured: StructuredGenerator
    tools: ToolCaller


class AgentContext(Deps):
    run: RunScope
    email_id: str | None  # the email a webhook run is about; None in chat
    chat: ChatScope | None = None  # the turn a chat run answers; None in webhook
    step_id: int | None = None  # set by the executor for the step in flight
    llm: LLMDeps
    taxonomy: TaxonomySource
    mail: MailReader
    labels: Labeler
    history: SenderHistorySource
    actions: ActionSink
    policy: PolicyReader
    kb: KbReader
    retriever: Retriever
    lookup: EmailLookup
    docs: DocCatalog
    pending: PendingReader
    rules: RuleReader
    cards: CardSink

    def for_step(self, step_id: int) -> Self:
        return self.model_copy(update={"step_id": step_id})

    def call(self) -> CallContext:
        """Context for an LLM call made on behalf of the step in flight."""
        return CallContext(
            agent_run_id=self.run.run_id,
            agent_step_id=self.step_id,
            email_ids=(self.email_id,) if self.email_id else (),
        )
