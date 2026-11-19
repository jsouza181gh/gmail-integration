"""Vocabulary shared by the agent runtime, its ports and its tools."""
from datetime import datetime
from typing import Literal, get_args

from src.llm.types import Schema

Trigger = Literal["webhook", "chat"]
ActionKind = Literal["mark_read", "star", "archive", "trash", "block_sender"]
PolicyTarget = ActionKind | Literal["apply_labels"]
PolicyDecision = Literal["auto", "suggest", "deny"]
RuleMode = Literal["suggest", "auto"]
ProposalOutcome = Literal["suggested", "auto", "denied", "duplicate"]

# Hard invariant above any database policy or rule: filters never run alone.
NEVER_AUTO: frozenset[ActionKind] = frozenset({"block_sender"})
ACTION_KINDS: tuple[ActionKind, ...] = get_args(ActionKind)


def as_action_kind(value: str) -> ActionKind | None:
    """Typed view of a stored action name; None for anything unknown."""
    for kind in ACTION_KINDS:
        if kind == value:
            return kind
    return None


class CategoryCount(Schema):
    category: str
    count: int


class ActionOutcomeCount(Schema):
    kind: ActionKind
    status: str
    count: int


class SenderStats(Schema):
    """What we know about a sender from our own records."""

    address: str
    total: int
    by_category: tuple[CategoryCount, ...] = ()
    actions: tuple[ActionOutcomeCount, ...] = ()
    last_seen: datetime | None = None


class RuleRef(Schema):
    rule_id: int
    name: str
    mode: RuleMode


class ProposalRef(Schema):
    action_id: int | None  # None when denied: no row is written
    kind: ActionKind
    email_id: str
    outcome: ProposalOutcome


class MailQuery(Schema):
    query: str | None = None
    label_ids: tuple[str, ...] = ()
    max_results: int = 10
    page_token: str | None = None
    include_spam_trash: bool = False


class MailItem(Schema):
    id: str
    thread_id: str | None
    sender: str
    subject: str
    date: str
    snippet: str
    labels: tuple[str, ...]


class MailPage(Schema):
    items: tuple[MailItem, ...]
    next_page_token: str | None
    result_size_estimate: int


class LabelInfo(Schema):
    id: str
    name: str
    type: str | None
