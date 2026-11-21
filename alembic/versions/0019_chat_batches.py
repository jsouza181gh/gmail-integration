"""chat batches: batch info on actions cards, continuation waiting on a card

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_cards") as batch:
        batch.add_column(sa.Column("batch_query", sa.Text))
        batch.add_column(sa.Column("batch_total", sa.Integer))
        batch.add_column(sa.Column("batch_no", sa.Integer))
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("wait_card_id", sa.Integer))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("wait_card_id")
    with op.batch_alter_table("chat_cards") as batch:
        batch.drop_column("batch_no")
        batch.drop_column("batch_total")
        batch.drop_column("batch_query")
