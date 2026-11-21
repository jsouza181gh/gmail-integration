"""jobs: backfill_attachment kind (history attachments run on the backfill key)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.create_check_constraint(
            "ck_jobs_backfill_attachment_has_attachment",
            "kind <> 'backfill_attachment' OR attachment_id IS NOT NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_backfill_attachment_has_attachment", type_="check")
