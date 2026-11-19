"""What the agent depends on, as runtime-checkable protocols.

Implementations live next to their infrastructure (Gmail, database); tests
use fakes. `AgentContext` validates every dependency against these.
"""
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from src.agent.types import (
    ActionKind,
    LabelInfo,
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


@runtime_checkable
class MailReader(Protocol):
    def list_messages(self, query: MailQuery) -> MailPage: ...

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
        run_id: int,
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
