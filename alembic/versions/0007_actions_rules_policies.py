"""tool policies (seeded), automation rules, suggested actions; jobs.action_id FK

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NOW = sa.func.now()
ACTION_KINDS = ("mark_read", "star", "archive", "trash", "block_sender")
TRIGGERS = ("webhook", "chat")
LIVE = sa.text("status IN ('pending', 'approved')")


def upgrade() -> None:
    policies = op.create_table(
        "tool_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("target", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("policy", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("target", "trigger"),
    )
    op.bulk_insert(
        policies,
        [{"target": "apply_labels", "trigger": t, "policy": "auto"} for t in TRIGGERS]
        + [{"target": k, "trigger": t, "policy": "suggest"} for k in ACTION_KINDS for t in TRIGGERS],
    )
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sender_address", sa.String(320)),
        sa.Column("sender_domain", sa.String(255)),
        sa.Column("category", sa.String(64)),
        sa.Column("subject_contains", sa.String(200)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("fired_count", sa.Integer, nullable=False),
        sa.Column("approved_count", sa.Integer, nullable=False),
        sa.Column("rejected_count", sa.Integer, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sender_address IS NOT NULL OR sender_domain IS NOT NULL"
            " OR category IS NOT NULL OR subject_contains IS NOT NULL",
            name="ck_automation_rules_has_condition",
        ),
    )
    op.create_table(
        "suggested_actions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("agent_runs.id")),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("automation_rules.id")),
        sa.Column("batch_id", sa.String(64)),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_suggested_actions_live", "suggested_actions", ["email_id", "kind"],
        unique=True, postgresql_where=LIVE, sqlite_where=LIVE,
    )
    op.create_index("ix_suggested_actions_status", "suggested_actions", ["status"])
    with op.batch_alter_table("jobs") as batch:
        batch.create_foreign_key(
            "fk_jobs_action_id", "suggested_actions", ["action_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_action_id", type_="foreignkey")
    op.drop_index("ix_suggested_actions_status", "suggested_actions")
    op.drop_index("uq_suggested_actions_live", "suggested_actions")
    op.drop_table("suggested_actions")
    op.drop_table("automation_rules")
    op.drop_table("tool_policies")
