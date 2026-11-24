"""gmail per account: oauth_clients and the credentials columns of gmail_accounts

The migration reads no file and no environment: the legacy token enters
through `python -m src.cli.gmail_import_token`, the platform client through
`python -m src.cli.oauth_client sync`.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

NAMING = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=False),
    )
    with op.batch_alter_table("gmail_accounts", naming_convention=NAMING) as batch:
        batch.add_column(sa.Column("oauth_client_id", sa.Integer()))
        batch.add_column(sa.Column("refresh_token_enc", sa.Text()))
        batch.add_column(sa.Column("scopes", sa.Text()))
        batch.add_column(sa.Column("last_sync_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_error", sa.Text()))
        batch.create_foreign_key(
            "gmail_accounts_oauth_client_id_fkey", "oauth_clients", ["oauth_client_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("gmail_accounts", naming_convention=NAMING) as batch:
        batch.drop_constraint("gmail_accounts_oauth_client_id_fkey", type_="foreignkey")
        batch.drop_column("last_error")
        batch.drop_column("last_sync_at")
        batch.drop_column("scopes")
        batch.drop_column("refresh_token_enc")
        batch.drop_column("oauth_client_id")
    op.drop_table("oauth_clients")
