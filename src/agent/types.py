"""Vocabulary shared by the agent runtime, its ports and its tools."""
from datetime import date, datetime
from typing import Annotated, Literal, get_args

from pydantic import Field

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


class EmailRef(Schema):
    """An email an assistant answer referenced (kept for the next turns)."""

    email_id: str
    subject: str | None
    sender: str | None


class MailHit(Schema):
    """One search result over the index (email or indexed document)."""

    kind: Literal["email", "document"]
    id: str  # email id, or attachment id as text
    date: str | None
    category: str | None  # email category, or document class/type
    sender: str | None  # email sender, or document issuer
    title: str
    score: float  # cosine similarity; 0 for keyword-only hits


class DocHit(Schema):
    id: int
    email_id: str
    doc_class: str | None
    doc_type: str | None
    title: str
    issuer: str | None
    amount_cents: int | None
    due_on: date | None
    filename: str
    mime_type: str
    size: int


# --- chat cards ---------------------------------------------------------------
# What a chat tool hands to the card sink; the user confirms or cancels it.

Verdict = Literal["approve", "reject"]
KbScopeName = Literal["classification", "decision"]
ChatChannel = Literal["chat", "audio"]  # how the user spoke in the turn


class RuleDraft(Schema):
    kind: Literal["rule"] = "rule"
    name: str
    action: ActionKind
    sender_address: str | None = None
    sender_domain: str | None = None
    category: str | None = None
    subject_contains: str | None = None
    unless_document: bool = False
    affected: int


class PreferenceDraft(Schema):
    kind: Literal["preference"] = "preference"
    scope: KbScopeName
    text: str


class PromoteDraft(Schema):
    kind: Literal["promote"] = "promote"
    rule_id: int


class PolicyDraft(Schema):
    kind: Literal["policy"] = "policy"
    target: PolicyTarget
    trigger: Trigger
    policy: PolicyDecision


class ActionsDraft(Schema):
    kind: Literal["actions"] = "actions"
    action_ids: tuple[int, ...]
    verdict: Verdict | None = None
    comment: str | None = None
    action: ActionKind | None = None  # proposals of one action (propose_email_actions)
    batch_query: str | None = None  # the Gmail search this batch came from
    batch_total: int | None = None  # its estimated size
    batch_no: int | None = None  # 1-based


class EmailsDraft(Schema):
    kind: Literal["emails"] = "emails"
    email_ids: tuple[str, ...]


class DocumentsDraft(Schema):
    kind: Literal["documents"] = "documents"
    attachment_ids: tuple[int, ...]


CardDraft = Annotated[
    RuleDraft | PreferenceDraft | PromoteDraft | PolicyDraft | ActionsDraft | EmailsDraft
    | DocumentsDraft,
    Field(discriminator="kind"),
]
