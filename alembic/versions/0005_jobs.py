"""jobs queue

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id")),
        sa.Column("action_id", sa.Integer),
        sa.Column("last_error", sa.Text),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind <> 'process_email' OR email_id IS NOT NULL",
            name="ck_jobs_process_email_has_email",
        ),
        sa.CheckConstraint(
            "kind <> 'execute_action' OR action_id IS NOT NULL",
            name="ck_jobs_execute_action_has_action",
        ),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "priority", "run_after"])


def downgrade() -> None:
    op.drop_index("ix_jobs_claim", "jobs")
    op.drop_table("jobs")
