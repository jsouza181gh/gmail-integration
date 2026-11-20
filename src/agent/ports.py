"""What the agent depends on, as runtime-checkable protocols.

Implementations live next to their infrastructure (Gmail, database); tests
use fakes. `AgentContext` validates every dependency against these.
"""
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from src.agent.rules import RuleCondition
from src.agent.types import (
    ActionKind,
    CardDraft,
    ChatChannel,
    DocHit,
    EmailRef,
    LabelInfo,
    MailHit,
    MailItem,
    MailPage,
    MailQuery,
    PolicyDecision,
    PolicyTarget,
    ProposalRef,
    RuleMode,
    RuleRef,
    SenderStats,
    Trigger,
)
from src.classification.taxonomy import Taxonomy
from src.db.rows import EmailRow, PendingActionRow, RuleRow
from src.llm.types import CallContext


@runtime_checkable
class MailReader(Protocol):
    def list_messages(self, query: MailQuery) -> MailPage: ...

    def item(self, email_id: str) -> MailItem | None: ...

    def get_body(self, email_id: str) -> str: ...

    def list_labels(self) -> tuple[LabelInfo, ...]: ...


@runtime_checkable
class Labeler(Protocol):
    def apply(self, email_id: str, names: Sequence[str]) -> tuple[str, ...]: ...


@runtime_checkable
class SenderHistorySource(Protocol):
    def stats(self, address: str) -> SenderStats: ...


@runtime_checkable
class ActionSink(Protocol):
    def propose(
        self,
        email_id: str,
        kind: ActionKind,
        reason: str,
        *,
        run_id: int | None,
        trigger: Trigger,
        rule: RuleRef | None = None,
    ) -> ProposalRef: ...


@runtime_checkable
class PolicyReader(Protocol):
    def resolve(
        self,
        target: PolicyTarget,
        trigger: Trigger,
        *,
        rule_mode: RuleMode | None = None,
    ) -> PolicyDecision: ...


@runtime_checkable
class KbReader(Protocol):
    def entries(self, scopes: Sequence[str]) -> tuple[str, ...]: ...


@runtime_checkable
class TaxonomySource(Protocol):
    def active_taxonomy(self) -> Taxonomy | None: ...

    def active_preferences(self, scope: str) -> list[str]: ...


# --- chat ---------------------------------------------------------------------

@runtime_checkable
class Retriever(Protocol):
    def search(self, query: str, k: int, call: CallContext) -> tuple[MailHit, ...]: ...


@runtime_checkable
class EmailLookup(Protocol):
    def email(self, email_id: str) -> EmailRow | None: ...

    def body(self, email_id: str) -> str | None: ...

    def remember(self, item: MailItem) -> EmailRow:
        """Store an email already fetched from Gmail (source='chat') if new."""
        ...

    def ensure(self, email_id: str) -> EmailRow | None:
        """The stored email, fetching it from Gmail (spam, trash, old mail)
        and storing it as source='chat' when unknown; None when Gmail no
        longer has it."""
        ...


@runtime_checkable
class DocCatalog(Protocol):
    def find(
        self, doc_class: str | None, doc_type: str | None, text: str | None, limit: int
    ) -> tuple[DocHit, ...]: ...

    def get(self, ids: Sequence[int]) -> tuple[DocHit, ...]: ...


@runtime_checkable
class PendingReader(Protocol):
    def pending(
        self,
        *,
        ids: Sequence[int] = (),
        sender: str | None = None,
        rule_id: int | None = None,
        kind: str | None = None,
    ) -> tuple[PendingActionRow, ...]: ...


@runtime_checkable
class RuleReader(Protocol):
    def existing(self, cond: RuleCondition, limit: int) -> tuple[list[str], int]: ...

    def list(self) -> tuple[RuleRow, ...]: ...

    def get(self, rule_id: int) -> RuleRow | None: ...

    def known_senders(self, address: str) -> tuple[int, tuple[str, ...]]: ...


@runtime_checkable
class RefSink(Protocol):
    def add_refs(self, message_id: int, refs: Sequence[EmailRef]) -> None: ...


@runtime_checkable
class CardSink(Protocol):
    def add_card(self, message_id: int, channel: ChatChannel, draft: CardDraft) -> int: ...
