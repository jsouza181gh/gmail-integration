"""login and invites: Google identity, optional password, per-user lockout

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("google_sub", sa.Text()))
        batch.add_column(sa.Column("onboarding_step", sa.String(16)))
        batch.add_column(sa.Column("locked_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("lock_cleared_at", sa.DateTime(timezone=True)))
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=True)
        batch.alter_column("totp_secret_enc", existing_type=sa.Text(), nullable=True)
        batch.create_unique_constraint("users_google_sub_key", ["google_sub"])
    with op.batch_alter_table("login_failures") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer()))
        batch.create_foreign_key("login_failures_user_id_fkey", "users", ["user_id"], ["id"])
        batch.create_index("ix_login_failures_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("login_failures") as batch:
        batch.drop_index("ix_login_failures_user_id")
        batch.drop_constraint("login_failures_user_id_fkey", type_="foreignkey")
        batch.drop_column("user_id")
    # Users created through invites have no password/TOTP yet: put placeholders
    # back so the NOT NULL constraints of the old schema hold.
    op.execute(sa.text("UPDATE users SET password_hash = '' WHERE password_hash IS NULL"))
    op.execute(sa.text("UPDATE users SET totp_secret_enc = '' WHERE totp_secret_enc IS NULL"))
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("users_google_sub_key", type_="unique")
        batch.alter_column("totp_secret_enc", existing_type=sa.Text(), nullable=False)
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=False)
        batch.drop_column("lock_cleared_at")
        batch.drop_column("locked_at")
        batch.drop_column("onboarding_step")
        batch.drop_column("google_sub")
    op.drop_table("invites")
