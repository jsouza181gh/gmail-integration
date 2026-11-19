from datetime import date as calendar_date
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.db.vector import Vector


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    page_token: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetch_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sender: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    date: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_relevant: Mapped[bool | None] = mapped_column(Boolean)
    deadline: Mapped[calendar_date | None] = mapped_column(Date)
    has_document: Mapped[bool | None] = mapped_column(Boolean)
    ephemeral: Mapped[bool | None] = mapped_column(Boolean)
    category_set_id: Mapped[int | None] = mapped_column(ForeignKey("category_sets.id"))
    # plain-text body (HTML reduced), stored by the indexer
    body: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RunEmail(Base):
    __tablename__ = "run_emails"

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WatchState(Base):
    __tablename__ = "watch_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    email_address: Mapped[str] = mapped_column(Text, nullable=False)
    history_id: Mapped[str] = mapped_column(String(32), nullable=False)
    expiration: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# --- taxonomy (versioned) -----------------------------------------------------

class CategorySet(Base):
    __tablename__ = "category_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CategoryFlag(Base):
    __tablename__ = "category_flags"
    __table_args__ = (UniqueConstraint("set_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("category_sets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("set_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("category_sets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class CategoryExample(Base):
    __tablename__ = "category_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


# --- knowledge base -----------------------------------------------------------

class KbEntry(Base):
    __tablename__ = "kb_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- LLM call log -------------------------------------------------------------

class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    agent_step_id: Mapped[int | None] = mapped_column(ForeignKey("agent_steps.id"), index=True)


class LlmCallMessage(Base):
    __tablename__ = "llm_call_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("llm_calls.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


# --- agent: config versions and run trail ------------------------------------

class AgentConfig(Base):
    """Immutable version of the agent's behaviour; one is active."""

    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decide_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    email_id: Mapped[str | None] = mapped_column(ForeignKey("emails.id"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("agent_configs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(Base):
    """One event of a run, in order. `detail` is audit text, never parsed back."""

    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GmailLabel(Base):
    """Cache of the Gmail ids of the labels the agent applies, by name."""

    __tablename__ = "gmail_labels"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --- agent: policies, suggested actions, automation rules ---------------------

class ToolPolicy(Base):
    """auto | suggest | deny per target (action kind or apply_labels) and trigger.

    Code invariants (`NEVER_AUTO`) sit above these rows.
    """

    __tablename__ = "tool_policies"
    __table_args__ = (UniqueConstraint("target", "trigger"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    policy: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AutomationRule(Base):
    """Deterministic condition (AND of the non-null fields) -> action."""

    __tablename__ = "automation_rules"
    __table_args__ = (
        CheckConstraint(
            "sender_address IS NOT NULL OR sender_domain IS NOT NULL"
            " OR category IS NOT NULL OR subject_contains IS NOT NULL",
            name="ck_automation_rules_has_condition",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sender_address: Mapped[str | None] = mapped_column(String(320))
    sender_domain: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64))
    subject_contains: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fired_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SuggestedAction(Base):
    __tablename__ = "suggested_actions"
    __table_args__ = (
        # one live proposal per (email, kind); rejected/executed ones may repeat
        Index(
            "uq_suggested_actions_live",
            "email_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
            sqlite_where=text("status IN ('pending', 'approved')"),
        ),
        Index("ix_suggested_actions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("automation_rules.id"))
    batch_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feedback(Base):
    """The user's verdict on a suggestion (and optional comment -> KB entry)."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("suggested_actions.id"), index=True)
    email_id: Mapped[str | None] = mapped_column(ForeignKey("emails.id"))
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    kb_entry_id: Mapped[int | None] = mapped_column(ForeignKey("kb_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Notification(Base):
    """Every alert attempt, including the ones suppressed by aggregation."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_kind_created", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    """Work queue; payload lives in typed nullable columns per kind."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'process_email' OR email_id IS NOT NULL",
            name="ck_jobs_process_email_has_email",
        ),
        CheckConstraint(
            "kind <> 'execute_action' OR action_id IS NOT NULL",
            name="ck_jobs_execute_action_has_action",
        ),
        CheckConstraint(
            "kind <> 'index_email' OR email_id IS NOT NULL",
            name="ck_jobs_index_email_has_email",
        ),
        CheckConstraint(
            "kind <> 'index_attachment' OR attachment_id IS NOT NULL",
            name="ck_jobs_index_attachment_has_attachment",
        ),
        CheckConstraint(
            "kind <> 'backfill_page' OR (run_id IS NOT NULL AND page_no IS NOT NULL)",
            name="ck_jobs_backfill_page_has_page",
        ),
        Index("ix_jobs_claim", "status", "priority", "run_after"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    email_id: Mapped[str | None] = mapped_column(ForeignKey("emails.id"))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("suggested_actions.id"))
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    page_no: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- indexing: embeddings and attachment documents ---------------------------

HNSW_OPS = {"embedding": "vector_cosine_ops"}


class EmailEmbedding(Base):
    __tablename__ = "email_embeddings"
    __table_args__ = (
        Index(
            "ix_email_embeddings_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
        ),
    )

    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[tuple[float, ...]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Attachment(Base):
    """A document attached to an email: metadata, extracted text, vector.

    The file itself is never stored; `part_id` locates it in the message
    (Gmail's attachmentId changes between reads, the partId does not).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("email_id", "part_id"),
        Index("ix_attachments_status", "status"),
        Index(
            "ix_attachments_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), nullable=False, index=True)
    part_id: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    doc_class: Mapped[str | None] = mapped_column(String(16))
    doc_type: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    issued_on: Mapped[calendar_date | None] = mapped_column(Date)
    due_on: Mapped[calendar_date | None] = mapped_column(Date)
    barcode: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[tuple[float, ...] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCallEmail(Base):
    __tablename__ = "llm_call_emails"

    call_id: Mapped[int] = mapped_column(ForeignKey("llm_calls.id"), primary_key=True)
    email_id: Mapped[str] = mapped_column(String(64), primary_key=True)
