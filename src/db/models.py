from datetime import date as calendar_date
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.db.scope import AccountOwned
from src.db.vector import Vector


class Base(DeclarativeBase):
    pass


def _email_fk() -> ForeignKeyConstraint:
    """(gmail_account_id, email_id) -> emails: a row can only point at its own account's mail."""
    return ForeignKeyConstraint(
        ["gmail_account_id", "email_id"], ["emails.gmail_account_id", "emails.id"]
    )


class Run(AccountOwned, Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    page_token: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetch_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Email(AccountOwned, Base):
    __tablename__ = "emails"
    __table_args__ = (
        Index("ix_emails_received_at", "received_at"),
        Index("ix_emails_category_received", "category", "received_at"),
        Index("ix_emails_relevant_deadline", "is_relevant", "deadline"),
    )

    gmail_account_id: Mapped[int] = mapped_column(
        ForeignKey("gmail_accounts.id"), primary_key=True
    )
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
    # inbox: came from the sync/backfill; chat: fetched on demand by a chat tool
    # (spam, trash, old mail) and kept out of the panel's lists
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="inbox",
                                        server_default="inbox")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RunEmail(AccountOwned, Base):
    __tablename__ = "run_emails"
    __table_args__ = (_email_fk(),)

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    email_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- taxonomy (versioned) -----------------------------------------------------

class CategorySet(AccountOwned, Base):
    __tablename__ = "category_sets"
    __table_args__ = (UniqueConstraint("gmail_account_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CategoryFlag(AccountOwned, Base):
    __tablename__ = "category_flags"
    __table_args__ = (UniqueConstraint("set_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("category_sets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)


class Category(AccountOwned, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("set_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("category_sets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class CategoryExample(AccountOwned, Base):
    __tablename__ = "category_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


# --- knowledge base -----------------------------------------------------------

class KbEntry(AccountOwned, Base):
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

class LlmCall(AccountOwned, Base):
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


class LlmCallMessage(AccountOwned, Base):
    __tablename__ = "llm_call_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("llm_calls.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


# --- agent: config versions and run trail ------------------------------------

class AgentConfig(AccountOwned, Base):
    """Immutable version of the agent's behaviour; one is active."""

    __tablename__ = "agent_configs"
    __table_args__ = (UniqueConstraint("gmail_account_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decide_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    chat_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRun(AccountOwned, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (_email_fk(),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    email_id: Mapped[str | None] = mapped_column(String(64), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("agent_configs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(AccountOwned, Base):
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


class GmailLabel(AccountOwned, Base):
    """Cache of the Gmail ids of the labels the agent applies, by name."""

    __tablename__ = "gmail_labels"

    gmail_account_id: Mapped[int] = mapped_column(
        ForeignKey("gmail_accounts.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --- agent: policies, suggested actions, automation rules ---------------------

class ToolPolicy(AccountOwned, Base):
    """auto | suggest | deny per target (action kind or apply_labels) and trigger.

    Code invariants (`NEVER_AUTO`) sit above these rows.
    """

    __tablename__ = "tool_policies"
    __table_args__ = (UniqueConstraint("gmail_account_id", "target", "trigger"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    policy: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AutomationRule(AccountOwned, Base):
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
    # "exceto quando tiver documento": skips emails flagged has_document
    unless_document: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fired_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SuggestedAction(AccountOwned, Base):
    __tablename__ = "suggested_actions"
    __table_args__ = (
        _email_fk(),
        # one live proposal per (email, kind); rejected/executed ones may repeat
        Index(
            "uq_suggested_actions_live",
            "gmail_account_id",
            "email_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
            sqlite_where=text("status IN ('pending', 'approved')"),
        ),
        Index("ix_suggested_actions_status", "status"),
        Index("ix_suggested_actions_email", "email_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(String(64), nullable=False)
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


class Feedback(AccountOwned, Base):
    """The user's verdict on a suggestion (and optional comment -> KB entry)."""

    __tablename__ = "feedback"
    __table_args__ = (_email_fk(),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("suggested_actions.id"), index=True)
    email_id: Mapped[str | None] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    kb_entry_id: Mapped[int | None] = mapped_column(ForeignKey("kb_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Notification(AccountOwned, Base):
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


class Job(AccountOwned, Base):
    """Work queue; payload lives in typed nullable columns per kind."""

    __tablename__ = "jobs"
    __table_args__ = (
        _email_fk(),
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
            "kind <> 'backfill_attachment' OR attachment_id IS NOT NULL",
            name="ck_jobs_backfill_attachment_has_attachment",
        ),
        CheckConstraint(
            "kind <> 'backfill_page' OR (run_id IS NOT NULL AND page_no IS NOT NULL)",
            name="ck_jobs_backfill_page_has_page",
        ),
        CheckConstraint(
            "kind <> 'chat_turn' OR chat_message_id IS NOT NULL",
            name="ck_jobs_chat_turn_has_message",
        ),
        Index("ix_jobs_claim", "status", "priority", "run_after"),
        UniqueConstraint("gmail_account_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    email_id: Mapped[str | None] = mapped_column(String(64))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("suggested_actions.id"))
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    page_no: Mapped[int | None] = mapped_column(Integer)
    chat_message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- indexing: embeddings and attachment documents ---------------------------

HNSW_OPS = {"embedding": "vector_cosine_ops"}


class EmailEmbedding(AccountOwned, Base):
    __tablename__ = "email_embeddings"
    __table_args__ = (
        _email_fk(),
        Index(
            "ix_email_embeddings_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
        ),
    )

    gmail_account_id: Mapped[int] = mapped_column(
        ForeignKey("gmail_accounts.id"), primary_key=True
    )
    email_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[tuple[float, ...]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Attachment(AccountOwned, Base):
    """A document attached to an email: metadata, extracted text, vector.

    The file itself is never stored; `part_id` locates it in the message
    (Gmail's attachmentId changes between reads, the partId does not).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        _email_fk(),
        UniqueConstraint("email_id", "part_id"),
        Index("ix_attachments_status", "status"),
        Index(
            "ix_attachments_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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


class LlmCallEmail(AccountOwned, Base):
    __tablename__ = "llm_call_emails"

    call_id: Mapped[int] = mapped_column(ForeignKey("llm_calls.id"), primary_key=True)
    email_id: Mapped[str] = mapped_column(String(64), primary_key=True)


# --- auth and security (single user) ------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member",
                                      server_default="member")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet(APP_SECRET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # STUB(fase 3): substituído no merge (0023 cria a coluna)
    onboarding_step: Mapped[str | None] = mapped_column(String(16))


class GmailAccount(Base):
    """A connected Gmail mailbox; owns every content row (`AccountOwned`)."""

    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    email_address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    history_id: Mapped[str | None] = mapped_column(String(32))
    watch_expiration: Mapped[int | None] = mapped_column(BigInteger)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # STUB(fase 2): substituído no merge (0022 cria as colunas)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Invite(Base):
    """STUB(fase 3): substituído no merge (0023 cria a tabela)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserLlmKey(Base):
    """STUB(fase 4): substituído no merge (0024 cria a tabela)."""

    __tablename__ = "user_llm_keys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    app_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    backfill_key_enc: Mapped[str | None] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AccountSettings(AccountOwned, Base):
    """Per-account runtime switches (were global `app_settings` keys)."""

    __tablename__ = "account_settings"

    gmail_account_id: Mapped[int] = mapped_column(
        ForeignKey("gmail_accounts.id"), primary_key=True
    )
    llm_mode: Mapped[str | None] = mapped_column(String(8))
    backfill_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    backfill_turbo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """One refresh token; a login starts a family, each refresh rotates it."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)


class LoginFailure(Base):
    __tablename__ = "login_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class IpBlock(Base):
    __tablename__ = "ip_blocks"

    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unblocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    # who the event is about; NULL = platform-wide (IP blocks, global lock), admin only
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AppSetting(Base):
    """Runtime switches (known keys only: llm_mode, global_lock)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigAudit(AccountOwned, Base):
    """Every configuration change, rendered as text before -> after."""

    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- chat ------------------------------------------------------------------

class ChatConversation(AccountOwned, Base):
    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(AccountOwned, Base):
    """One turn side: the user's words (text or audio) or the agent's answer."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    reply_to: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    # user messages: the model the user pinned for this turn (None = the chain)
    model: Mapped[str | None] = mapped_column(String(64))
    # a continuation answer waiting for this card's approved actions to run
    # (no FK: chat_cards already points here)
    wait_card_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessageRef(AccountOwned, Base):
    """An email an assistant answer referenced, so later turns can say "those"."""

    __tablename__ = "chat_message_refs"
    __table_args__ = (_email_fk(),)

    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)


class ChatAudio(AccountOwned, Base):
    """The original recording of a voice turn."""

    __tablename__ = "chat_audio"

    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), primary_key=True)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatCard(AccountOwned, Base):
    """Something the agent shows or proposes in a turn; typed columns per kind."""

    __tablename__ = "chat_cards"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'rule' OR (action IS NOT NULL AND rule_name IS NOT NULL AND ("
            "sender_address IS NOT NULL OR sender_domain IS NOT NULL"
            " OR category IS NOT NULL OR subject_contains IS NOT NULL))",
            name="ck_chat_cards_rule",
        ),
        CheckConstraint(
            "kind <> 'preference' OR (kb_text IS NOT NULL AND kb_scope IS NOT NULL)",
            name="ck_chat_cards_preference",
        ),
        CheckConstraint("kind <> 'promote' OR rule_id IS NOT NULL", name="ck_chat_cards_promote"),
        CheckConstraint(
            "kind <> 'policy' OR (policy_target IS NOT NULL AND policy_trigger IS NOT NULL"
            " AND policy_value IS NOT NULL)",
            name="ck_chat_cards_policy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    rule_name: Mapped[str | None] = mapped_column(String(120))
    sender_address: Mapped[str | None] = mapped_column(String(320))
    sender_domain: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64))
    subject_contains: Mapped[str | None] = mapped_column(String(200))
    unless_document: Mapped[bool | None] = mapped_column(Boolean)
    action: Mapped[str | None] = mapped_column(String(32))
    apply_existing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    affected: Mapped[int | None] = mapped_column(Integer)
    kb_scope: Mapped[str | None] = mapped_column(String(32))
    kb_text: Mapped[str | None] = mapped_column(Text)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("automation_rules.id"))
    policy_target: Mapped[str | None] = mapped_column(String(32))
    policy_trigger: Mapped[str | None] = mapped_column(String(16))
    policy_value: Mapped[str | None] = mapped_column(String(16))
    verdict: Mapped[str | None] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)
    created_rule_id: Mapped[int | None] = mapped_column(ForeignKey("automation_rules.id"))
    kb_entry_id: Mapped[int | None] = mapped_column(ForeignKey("kb_entries.id"))
    # actions cards proposed from a Gmail search: which batch of how many
    batch_query: Mapped[str | None] = mapped_column(Text)
    batch_total: Mapped[int | None] = mapped_column(Integer)
    batch_no: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatCardItem(AccountOwned, Base):
    """One email, suggestion or document listed on a card."""

    __tablename__ = "chat_card_items"
    __table_args__ = (
        _email_fk(),
        CheckConstraint(
            "(CASE WHEN email_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN action_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN attachment_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_chat_card_items_one_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("chat_cards.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    email_id: Mapped[str | None] = mapped_column(String(64))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("suggested_actions.id"))
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
