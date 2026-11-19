"""llm_calls: link to agent run and step

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19

Plain indexed columns for now; the foreign keys to agent_runs/agent_steps
come with the migration that creates those tables.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.add_column(sa.Column("agent_run_id", sa.Integer))
        batch.add_column(sa.Column("agent_step_id", sa.Integer))
    op.create_index("ix_llm_calls_agent_run_id", "llm_calls", ["agent_run_id"])
    op.create_index("ix_llm_calls_agent_step_id", "llm_calls", ["agent_step_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_agent_step_id", "llm_calls")
    op.drop_index("ix_llm_calls_agent_run_id", "llm_calls")
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_column("agent_step_id")
        batch.drop_column("agent_run_id")
