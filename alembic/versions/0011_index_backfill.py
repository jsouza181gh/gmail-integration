"""pgvector; email bodies + embeddings; attachments; jobs for indexing/backfill

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from src.db.vector import Vector

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

HNSW_OPS = {"embedding": "vector_cosine_ops"}


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    with op.batch_alter_table("emails") as batch:
        batch.add_column(sa.Column("body", sa.Text))
        batch.add_column(sa.Column("indexed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("index_error", sa.Text))

    op.create_table(
        "email_embeddings",
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id"), primary_key=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_email_embeddings_hnsw", "email_embeddings", ["embedding"],
        postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
    )

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email_id", sa.String(64), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("part_id", sa.String(32), nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("skip_reason", sa.String(64)),
        sa.Column("doc_class", sa.String(16)),
        sa.Column("doc_type", sa.String(32)),
        sa.Column("title", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("issuer", sa.Text),
        sa.Column("amount_cents", sa.BigInteger),
        sa.Column("issued_on", sa.Date),
        sa.Column("due_on", sa.Date),
        sa.Column("barcode", sa.String(64)),
        sa.Column("text", sa.Text),
        sa.Column("embedding", Vector()),
        sa.Column("embedding_model", sa.String(64)),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email_id", "part_id"),
    )
    op.create_index("ix_attachments_email_id", "attachments", ["email_id"])
    op.create_index("ix_attachments_status", "attachments", ["status"])
    op.create_index(
        "ix_attachments_hnsw", "attachments", ["embedding"],
        postgresql_using="hnsw", postgresql_ops=HNSW_OPS,
    )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("attachment_id", sa.Integer))
        batch.add_column(sa.Column("run_id", sa.Integer))
        batch.add_column(sa.Column("page_no", sa.Integer))
        batch.create_foreign_key(
            "fk_jobs_attachment_id", "attachments", ["attachment_id"], ["id"]
        )
        batch.create_foreign_key("fk_jobs_run_id", "runs", ["run_id"], ["id"])
        batch.create_check_constraint(
            "ck_jobs_index_email_has_email", "kind <> 'index_email' OR email_id IS NOT NULL"
        )
        batch.create_check_constraint(
            "ck_jobs_index_attachment_has_attachment",
            "kind <> 'index_attachment' OR attachment_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_jobs_backfill_page_has_page",
            "kind <> 'backfill_page' OR (run_id IS NOT NULL AND page_no IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_backfill_page_has_page", type_="check")
        batch.drop_constraint("ck_jobs_index_attachment_has_attachment", type_="check")
        batch.drop_constraint("ck_jobs_index_email_has_email", type_="check")
        batch.drop_constraint("fk_jobs_run_id", type_="foreignkey")
        batch.drop_constraint("fk_jobs_attachment_id", type_="foreignkey")
        batch.drop_column("page_no")
        batch.drop_column("run_id")
        batch.drop_column("attachment_id")

    op.drop_index("ix_attachments_hnsw", "attachments")
    op.drop_index("ix_attachments_status", "attachments")
    op.drop_index("ix_attachments_email_id", "attachments")
    op.drop_table("attachments")
    op.drop_index("ix_email_embeddings_hnsw", "email_embeddings")
    op.drop_table("email_embeddings")

    with op.batch_alter_table("emails") as batch:
        batch.drop_column("index_error")
        batch.drop_column("indexed_at")
        batch.drop_column("body")
