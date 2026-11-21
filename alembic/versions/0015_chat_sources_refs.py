"""emails.source (inbox|chat) and chat_message_refs

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("emails") as batch:
        batch.add_column(
            sa.Column("source", sa.String(16), nullable=False, server_default="inbox")
        )
    op.create_table(
        "chat_message_refs",
        sa.Column("message_id", sa.Integer, sa.ForeignKey("chat_messages.id"), primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("sender", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("chat_message_refs")
    with op.batch_alter_table("emails") as batch:
        batch.drop_column("source")
