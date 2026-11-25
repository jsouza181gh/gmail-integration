"""user_llm_keys: each user's own Gemini key(s)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_llm_keys",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("app_key_enc", sa.Text, nullable=False),
        sa.Column("backfill_key_enc", sa.Text),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("user_llm_keys")
