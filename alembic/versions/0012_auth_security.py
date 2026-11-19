"""auth (user, recovery codes, refresh families), lockouts, security events,
runtime settings, config audit

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("totp_secret_enc", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("password_changed_at", TS, nullable=False),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", TS),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("rotated_at", TS),
        sa.Column("revoked_at", TS),
        sa.Column("revoke_reason", sa.String(32)),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.Text),
    )
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_table(
        "login_failures",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_login_failures_ip", "login_failures", ["ip"])
    op.create_index("ix_login_failures_created_at", "login_failures", ["created_at"])
    op.create_table(
        "ip_blocks",
        sa.Column("ip", sa.String(64), primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("unblocked_at", TS),
    )
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_security_events_kind", "security_events", ["kind"])
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "config_audit",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entity", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("before", sa.Text),
        sa.Column("after", sa.Text),
        sa.Column("created_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("config_audit")
    op.drop_table("app_settings")
    op.drop_index("ix_security_events_created_at", "security_events")
    op.drop_index("ix_security_events_kind", "security_events")
    op.drop_table("security_events")
    op.drop_table("ip_blocks")
    op.drop_index("ix_login_failures_created_at", "login_failures")
    op.drop_index("ix_login_failures_ip", "login_failures")
    op.drop_table("login_failures")
    op.drop_index("ix_auth_sessions_family_id", "auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("recovery_codes")
    op.drop_table("users")
