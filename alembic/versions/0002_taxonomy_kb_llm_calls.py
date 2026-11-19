"""taxonomy, kb and llm call log; email flags

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "category_sets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer, nullable=False, unique=True),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "category_flags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("set_id", sa.Integer, sa.ForeignKey("category_sets.id"), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.UniqueConstraint("set_id", "name"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("set_id", sa.Integer, sa.ForeignKey("category_sets.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.Column("color", sa.String(16)),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("set_id", "name"),
    )
    op.create_table(
        "category_examples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
    )
    op.create_table(
        "kb_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_kind", sa.String(16)),
        sa.Column("error", sa.Text),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("total_tokens", sa.Integer, nullable=False),
        sa.Column("cached_input_tokens", sa.Integer, nullable=False),
        sa.Column("reasoning_tokens", sa.Integer, nullable=False),
        sa.Column("audio_input_tokens", sa.Integer, nullable=False),
        sa.Column("response_text", sa.Text),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id")),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_table(
        "llm_call_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("llm_calls.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
    )
    op.create_index("ix_llm_call_messages_call_id", "llm_call_messages", ["call_id"])
    op.create_table(
        "llm_call_emails",
        sa.Column("call_id", sa.Integer, sa.ForeignKey("llm_calls.id"), primary_key=True),
        sa.Column("email_id", sa.String(64), primary_key=True),
    )

    with op.batch_alter_table("emails") as batch:
        batch.add_column(sa.Column("received_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("is_relevant", sa.Boolean))
        batch.add_column(sa.Column("deadline", sa.Date))
        batch.add_column(sa.Column("has_document", sa.Boolean))
        batch.add_column(sa.Column("ephemeral", sa.Boolean))
        batch.add_column(sa.Column("category_set_id", sa.Integer))
        batch.create_foreign_key(
            "fk_emails_category_set_id", "category_sets", ["category_set_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("emails") as batch:
        batch.drop_constraint("fk_emails_category_set_id", type_="foreignkey")
        for col in (
            "category_set_id", "ephemeral", "has_document", "deadline", "is_relevant", "received_at",
        ):
            batch.drop_column(col)
    op.drop_table("llm_call_emails")
    op.drop_index("ix_llm_call_messages_call_id", "llm_call_messages")
    op.drop_table("llm_call_messages")
    op.drop_index("ix_llm_calls_created_at", "llm_calls")
    op.drop_table("llm_calls")
    op.drop_table("kb_entries")
    op.drop_table("category_examples")
    op.drop_table("categories")
    op.drop_table("category_flags")
    op.drop_table("category_sets")
