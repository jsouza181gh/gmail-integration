"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("page_token", sa.Text),
        sa.Column("pages", sa.Integer, nullable=False),
        sa.Column("fetch_done", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "emails",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("sender", sa.Text),
        sa.Column("subject", sa.Text),
        sa.Column("date", sa.Text),
        sa.Column("snippet", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "run_emails",
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id"), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "watch_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email_address", sa.Text, nullable=False),
        sa.Column("history_id", sa.String(32), nullable=False),
        sa.Column("expiration", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )


def downgrade() -> None:
    op.drop_table("watch_state")
    op.drop_table("run_emails")
    op.drop_table("emails")
    op.drop_table("runs")
