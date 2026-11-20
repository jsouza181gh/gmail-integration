"""Agent tools and the registry that pairs each one with its slices.

`SLICES` names, per tool, the only context and state schemas it receives;
`build_registry` holds the projections from the run's root context/state.
"""
from pydantic import BaseModel

from src.agent.context import AgentContext, ChatScope
from src.agent.registry import AgentTool, Registry, ToolEntry
from src.agent.state import AgentState, EmailFacts
from src.agent.tools.act import ActCtx, ActState, propose_action
from src.agent.tools.chat_cards import (
    CardCtx,
    EmailsCtx,
    PendingCtx,
    PromoteCtx,
    ProposeActionCtx,
    RuleCardCtx,
    ShareCtx,
    decide_pending,
    list_pending,
    propose_email_actions,
    propose_policy,
    propose_preference,
    propose_promotion,
    propose_rule,
    share_documents,
    show_emails,
)
from src.agent.tools.chat_read import (
    DocsCtx,
    ReadCtx,
    RulesCtx,
    SearchCtx,
    StatsCtx,
    find_documents,
    list_rules,
    read_email,
    search_mail,
    sender_stats,
)
from src.agent.tools.classify import ClassifyCtx, ClassifyState, classify_email
from src.agent.tools.labels import LabelCtx, LabelState, apply_labels
from src.agent.tools.mail import (
    BodyCtx,
    BodyState,
    ListCtx,
    MailCtx,
    NoState,
    get_email_body,
    list_emails,
    list_labels,
)
from src.agent.tools.sender import SenderCtx, SenderState, sender_history
from src.agent.types import Trigger

WEBHOOK: frozenset[Trigger] = frozenset({"webhook"})
CHAT: frozenset[Trigger] = frozenset({"chat"})

SLICES: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "classify_email": (ClassifyCtx, ClassifyState),
    "get_email_body": (BodyCtx, BodyState),
    "sender_history": (SenderCtx, SenderState),
    "list_emails": (ListCtx, NoState),
    "list_labels": (MailCtx, NoState),
    "propose_action": (ActCtx, ActState),
    "apply_labels": (LabelCtx, LabelState),
    "search_mail": (SearchCtx, NoState),
    "read_email": (ReadCtx, NoState),
    "find_documents": (DocsCtx, NoState),
    "list_rules": (RulesCtx, NoState),
    "sender_stats": (StatsCtx, NoState),
    "show_emails": (EmailsCtx, NoState),
    "share_documents": (ShareCtx, NoState),
    "list_pending": (PendingCtx, NoState),
    "decide_pending": (PendingCtx, NoState),
    "propose_email_actions": (ProposeActionCtx, NoState),
    "propose_rule": (RuleCardCtx, NoState),
    "propose_preference": (CardCtx, NoState),
    "propose_promotion": (PromoteCtx, NoState),
    "propose_policy": (CardCtx, NoState),
}


def focus_id(ctx: AgentContext) -> str:
    """The email a tool without an email argument works on."""
    if ctx.email_id is None:
        raise ValueError("esta tool precisa de um e-mail em foco")
    return ctx.email_id


def focus(ctx: AgentContext, state: AgentState) -> EmailFacts:
    return state.emails[focus_id(ctx)]


def chat_of(ctx: AgentContext) -> ChatScope:
    """The chat turn a card tool writes to."""
    if ctx.chat is None:
        raise ValueError("esta tool só funciona numa conversa do chat")
    return ctx.chat


def _no_state(c: AgentContext, s: AgentState) -> NoState:
    return NoState()


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
            contexts=WEBHOOK,
            offered=frozenset(),  # planned by the webhook flow
        ),
        AgentTool(
            get_email_body,
            context_of=lambda c: BodyCtx(mail=c.mail, email_id=focus_id(c)),
            state_of=lambda c, s: BodyState(body=focus(c, s).body),
            contexts=WEBHOOK,
        ),
        AgentTool(
            sender_history,
            context_of=lambda c: SenderCtx(history=c.history),
            state_of=lambda c, s: SenderState(
                email_id=focus_id(c), sender=focus(c, s).email.sender
            ),
            contexts=WEBHOOK,
            offered=frozenset(),  # planned by the webhook flow; chat has sender_stats
        ),
        AgentTool(
            propose_action,
            context_of=lambda c: ActCtx(actions=c.actions, email_id=focus_id(c), run=c.run),
            state_of=lambda c, s: ActState(proposals=s.proposals),
            contexts=WEBHOOK,
        ),
        AgentTool(
            apply_labels,
            context_of=lambda c: LabelCtx(
                labels=c.labels,
                email_id=focus_id(c),
                policy=c.policy.resolve("apply_labels", c.run.trigger),
            ),
            state_of=lambda c, s: LabelState(classification=focus(c, s).classification),
            contexts=WEBHOOK,
            offered=frozenset(),  # the flow applies labels right after classifying
        ),
        AgentTool(
            list_emails,
            context_of=lambda c: ListCtx(
                mail=c.mail, lookup=c.lookup, refs=c.refs,
                message_id=c.chat.message_id if c.chat else None,
            ),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            list_labels,
            context_of=lambda c: MailCtx(mail=c.mail),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            search_mail,
            context_of=lambda c: SearchCtx(
                retriever=c.retriever, call=c.call(), refs=c.refs,
                message_id=c.chat.message_id if c.chat else None,
            ),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            read_email,
            context_of=lambda c: ReadCtx(lookup=c.lookup, mail=c.mail),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            find_documents,
            context_of=lambda c: DocsCtx(docs=c.docs),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            list_rules,
            context_of=lambda c: RulesCtx(rules=c.rules),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            sender_stats,
            context_of=lambda c: StatsCtx(history=c.history),
            state_of=lambda c, s: NoState(),
            contexts=CHAT,
        ),
        AgentTool(
            show_emails,
            context_of=lambda c: EmailsCtx(cards=c.cards, chat=chat_of(c), lookup=c.lookup,
                                           refs=c.refs),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            share_documents,
            context_of=lambda c: ShareCtx(cards=c.cards, chat=chat_of(c), docs=c.docs),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            list_pending,
            context_of=lambda c: PendingCtx(cards=c.cards, chat=chat_of(c), pending=c.pending),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            decide_pending,
            context_of=lambda c: PendingCtx(cards=c.cards, chat=chat_of(c), pending=c.pending),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            propose_email_actions,
            context_of=lambda c: ProposeActionCtx(
                cards=c.cards, chat=chat_of(c), actions=c.actions, lookup=c.lookup, mail=c.mail,
                run=c.run, refs=c.refs,
            ),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            propose_rule,
            context_of=lambda c: RuleCardCtx(
                cards=c.cards, chat=chat_of(c), rules=c.rules, taxonomy=c.taxonomy
            ),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            propose_preference,
            context_of=lambda c: CardCtx(cards=c.cards, chat=chat_of(c)),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            propose_promotion,
            context_of=lambda c: PromoteCtx(cards=c.cards, chat=chat_of(c), rules=c.rules),
            state_of=_no_state,
            contexts=CHAT,
        ),
        AgentTool(
            propose_policy,
            context_of=lambda c: CardCtx(cards=c.cards, chat=chat_of(c)),
            state_of=_no_state,
            contexts=CHAT,
        ),
    ]
    return Registry(tools)
