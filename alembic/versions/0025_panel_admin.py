"""security_events.user_id: per-user security pages

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-24

On feat/mt-f5 revision 0024 is a stub (0024_stub_f2_f3_f4_deps.py) standing in
for phases 2-4; the integrator deletes it at merge and this file stays as is.
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("security_events") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer()))
        batch.create_foreign_key("fk_security_events_user", "users", ["user_id"], ["id"])
        batch.create_index("ix_security_events_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("security_events") as batch:
        batch.drop_index("ix_security_events_user_id")
        batch.drop_constraint("fk_security_events_user", type_="foreignkey")
        batch.drop_column("user_id")
