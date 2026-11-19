"""Agent tools and the registry that pairs each one with its slices.

`SLICES` names, per tool, the only context and state schemas it receives;
`build_registry` holds the projections from the run's root context/state.
"""
from pydantic import BaseModel

from src.agent.context import AgentContext
from src.agent.registry import AgentTool, Registry, ToolEntry
from src.agent.state import AgentState, EmailFacts
from src.agent.tools.classify import ClassifyCtx, ClassifyState, classify_email
from src.agent.tools.mail import (
    BodyCtx,
    BodyState,
    MailCtx,
    NoState,
    get_email_body,
    list_emails,
    list_labels,
)
from src.agent.tools.sender import SenderCtx, SenderState, sender_history
from src.agent.types import Trigger

BOTH: frozenset[Trigger] = frozenset({"webhook", "chat"})
CHAT: frozenset[Trigger] = frozenset({"chat"})

SLICES: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "classify_email": (ClassifyCtx, ClassifyState),
    "get_email_body": (BodyCtx, BodyState),
    "sender_history": (SenderCtx, SenderState),
    "list_emails": (MailCtx, NoState),
    "list_labels": (MailCtx, NoState),
}


def focus_id(ctx: AgentContext) -> str:
    """The email a tool without an email argument works on."""
    if ctx.email_id is None:
        raise ValueError("esta tool precisa de um e-mail em foco")
    return ctx.email_id


def focus(ctx: AgentContext, state: AgentState) -> EmailFacts:
    return state.emails[focus_id(ctx)]


def build_registry() -> Registry:
    tools: list[ToolEntry] = [
        AgentTool(
            classify_email,
            context_of=lambda c: ClassifyCtx(
                llm=c.llm.structured, taxonomy=c.taxonomy, call=c.call()
            ),
            state_of=lambda c, s: ClassifyState(
                email=focus(c, s).email, body=focus(c, s).body
            ),
            contexts=BOTH,
            offered=CHAT,  # planned by the webhook flow, chosen by the model in chat
        ),
        AgentTool(
            get_email_body,
            context_of=lambda c: BodyCtx(mail=c.mail, email_id=focus_id(c)),
            state_of=lambda c, s: BodyState(body=focus(c, s).body),
            contexts=BOTH,
        ),
        AgentTool(
            sender_history,
            context_of=lambda c: SenderCtx(history=c.history),
            state_of=lambda c, s: SenderState(
                email_id=focus_id(c), sender=focus(c, s).email.sender
            ),
            contexts=BOTH,
            offered=CHAT,
        ),
        AgentTool(
            list_emails,
            context_of=lambda c: MailCtx(mail=c.mail),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            list_labels,
            context_of=lambda c: MailCtx(mail=c.mail),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
    ]
    return Registry(tools)
