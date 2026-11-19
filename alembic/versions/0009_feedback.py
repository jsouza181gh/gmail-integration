"""feedback on suggested actions

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("action_id", sa.Integer, sa.ForeignKey("suggested_actions.id")),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id")),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("text", sa.Text),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("kb_entry_id", sa.Integer, sa.ForeignKey("kb_entries.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_action_id", "feedback", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_action_id", "feedback")
    op.drop_table("feedback")
