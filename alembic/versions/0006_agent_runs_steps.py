"""agent config versions, runs and steps; link llm_calls to them

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "agent_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer, nullable=False, unique=True),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("decide_prompt", sa.Text, nullable=False),
        sa.Column("max_turns", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id")),
        sa.Column("config_id", sa.Integer, sa.ForeignKey("agent_configs.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_runs_email_id", "agent_runs", ["email_id"])
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "position"),
    )
    with op.batch_alter_table("llm_calls") as batch:
        batch.create_foreign_key(
            "fk_llm_calls_agent_run_id", "agent_runs", ["agent_run_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_llm_calls_agent_step_id", "agent_steps", ["agent_step_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_constraint("fk_llm_calls_agent_step_id", type_="foreignkey")
        batch.drop_constraint("fk_llm_calls_agent_run_id", type_="foreignkey")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_runs_email_id", "agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("agent_configs")
