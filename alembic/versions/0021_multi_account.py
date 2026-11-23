"""multi-account: gmail_accounts owns every content row

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("email_address", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("history_id", sa.String(32)),
        sa.Column("watch_expiration", sa.BigInteger()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("gmail_accounts")
