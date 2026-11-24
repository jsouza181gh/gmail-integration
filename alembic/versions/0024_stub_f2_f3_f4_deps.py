"""STUB(fases 2-4): schema que o painel (fase 5) consome; apagar no merge

Revision ID: 0024
Revises: 0021
Create Date: 2026-11-24

Este arquivo só existe no branch feat/mt-f5. No merge, as migrações reais
0022 (gmail_accounts.last_sync_at/last_error), 0023 (users.onboarding_step,
invites) e 0024 (user_llm_keys) substituem este arquivo inteiro; a 0025 já
aponta para a revisão "0024".
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gmail_accounts") as batch:
        batch.add_column(sa.Column("last_sync_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_error", sa.Text()))
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("onboarding_step", sa.String(16)))
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "user_llm_keys",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("app_key_enc", sa.Text(), nullable=False),
        sa.Column("backfill_key_enc", sa.Text()),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("user_llm_keys")
    op.drop_table("invites")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("onboarding_step")
    with op.batch_alter_table("gmail_accounts") as batch:
        batch.drop_column("last_error")
        batch.drop_column("last_sync_at")
