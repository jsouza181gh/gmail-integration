"""chat_messages.model: the model the user pinned for a turn

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("model", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("model")
