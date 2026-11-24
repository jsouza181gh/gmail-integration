"""user_llm_keys: each user's own Gemini key(s)

Revision ID: 0024
Revises: 0021
Create Date: 2026-11-24

down_revision is 0021 on the feat/mt-f4 branch; the integrator rechains it
to 0023 (phases 2 and 3 take 0022 and 0023).
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0021"
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
