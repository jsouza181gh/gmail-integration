"""seed agent config v1 (decide prompt); gmail label id cache

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from src.agent.prompts import DECIDE_PROMPT_V1

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

MAX_TURNS_V1 = 4


def upgrade() -> None:
    op.create_table(
        "gmail_labels",
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("gmail_id", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    configs = sa.table(
        "agent_configs",
        sa.column("version", sa.Integer),
        sa.column("active", sa.Boolean),
        sa.column("decide_prompt", sa.Text),
        sa.column("max_turns", sa.Integer),
    )
    op.bulk_insert(
        configs,
        [{"version": 1, "active": True, "decide_prompt": DECIDE_PROMPT_V1, "max_turns": MAX_TURNS_V1}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_configs WHERE version = 1")
    op.drop_table("gmail_labels")
