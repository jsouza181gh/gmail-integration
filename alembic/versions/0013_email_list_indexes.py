"""indexes for the panel's email lists (45k rows after the backfill)

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_emails_received_at", "emails", ["received_at"])
    op.create_index("ix_emails_category_received", "emails", ["category", "received_at"])
    op.create_index("ix_emails_relevant_deadline", "emails", ["is_relevant", "deadline"])
    op.create_index("ix_suggested_actions_email", "suggested_actions", ["email_id"])


def downgrade() -> None:
    op.drop_index("ix_suggested_actions_email", "suggested_actions")
    op.drop_index("ix_emails_relevant_deadline", "emails")
    op.drop_index("ix_emails_category_received", "emails")
    op.drop_index("ix_emails_received_at", "emails")
