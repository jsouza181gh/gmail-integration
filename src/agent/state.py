"""AgentState: what a run has learned so far, plus the typed updates to it.

Tools never mutate state. They return a `StateUpdate`; the runtime folds it
in with `reduce`, so every change is explicit, typed and traceable.
"""
from typing import Annotated, Literal

from pydantic import Field

from src.agent.types import ProposalRef, SenderStats
from src.classification.taxonomy import Classification
from src.db.rows import EmailRow
from src.llm.types import Schema


class EmailFacts(Schema):
    email: EmailRow
    body: str | None = None
    classification: Classification | None = None
    sender: SenderStats | None = None
    labels: tuple[str, ...] = ()


class AgentState(Schema):
    emails: dict[str, EmailFacts]
    proposals: tuple[ProposalRef, ...] = ()
    turns_used: int = 0


class BodyFetched(Schema):
    kind: Literal["body_fetched"] = "body_fetched"
    email_id: str
    body: str


class Classified(Schema):
    kind: Literal["classified"] = "classified"
    email_id: str
    classification: Classification


class SenderLoaded(Schema):
    kind: Literal["sender_loaded"] = "sender_loaded"
    email_id: str
    stats: SenderStats


class LabelsApplied(Schema):
    kind: Literal["labels_applied"] = "labels_applied"
    email_id: str
    labels: tuple[str, ...]


class ActionProposed(Schema):
    kind: Literal["action_proposed"] = "action_proposed"
    ref: ProposalRef


StateUpdate = Annotated[
    BodyFetched | Classified | SenderLoaded | LabelsApplied | ActionProposed,
    Field(discriminator="kind"),
]


def _with_facts(state: AgentState, email_id: str, **changes: object) -> AgentState:
    facts = state.emails[email_id].model_copy(update=changes)
    return state.model_copy(update={"emails": {**state.emails, email_id: facts}})


def reduce(state: AgentState, update: StateUpdate) -> AgentState:
    match update:
        case BodyFetched():
            return _with_facts(state, update.email_id, body=update.body)
        case Classified():
            return _with_facts(state, update.email_id, classification=update.classification)
        case SenderLoaded():
            return _with_facts(state, update.email_id, sender=update.stats)
        case LabelsApplied():
            return _with_facts(state, update.email_id, labels=update.labels)
        case ActionProposed():
            return state.model_copy(update={"proposals": (*state.proposals, update.ref)})
