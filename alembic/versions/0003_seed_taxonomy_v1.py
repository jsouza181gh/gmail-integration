"""seed taxonomy v1 and classification preferences

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from src.classification.seed import V1, V1_PREFERENCES

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

category_sets = sa.table(
    "category_sets",
    sa.column("id", sa.Integer),
    sa.column("version", sa.Integer),
    sa.column("active", sa.Boolean),
)
category_flags = sa.table(
    "category_flags",
    sa.column("set_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("definition", sa.Text),
)
categories = sa.table(
    "categories",
    sa.column("id", sa.Integer),
    sa.column("set_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("definition", sa.Text),
    sa.column("color", sa.String),
    sa.column("position", sa.Integer),
)
category_examples = sa.table(
    "category_examples",
    sa.column("category_id", sa.Integer),
    sa.column("position", sa.Integer),
    sa.column("text", sa.Text),
)
kb_entries = sa.table(
    "kb_entries",
    sa.column("kind", sa.String),
    sa.column("scope", sa.String),
    sa.column("text", sa.Text),
    sa.column("source", sa.String),
    sa.column("active", sa.Boolean),
)


def _id(table: sa.TableClause, **where: object) -> int:
    stmt = sa.select(table.c.id).where(*(table.c[k] == v for k, v in where.items()))
    value = op.get_bind().execute(stmt).scalar_one()
    assert isinstance(value, int)
    return value


def upgrade() -> None:
    op.execute(category_sets.update().values(active=False))
    op.bulk_insert(category_sets, [{"version": 1, "active": True}])
    set_id = _id(category_sets, version=1)
    op.bulk_insert(
        category_flags,
        [{"set_id": set_id, "name": f.name, "definition": f.definition} for f in V1.flags],
    )
    for pos, c in enumerate(V1.categories):
        op.bulk_insert(
            categories,
            [{"set_id": set_id, "name": c.name, "definition": c.definition,
              "color": c.color, "position": pos}],
        )
        cat_id = _id(categories, set_id=set_id, name=c.name)
        op.bulk_insert(
            category_examples,
            [{"category_id": cat_id, "position": i, "text": t} for i, t in enumerate(c.examples)],
        )
    op.bulk_insert(
        kb_entries,
        [{"kind": "preference", "scope": p.scope, "text": p.text, "source": "seed", "active": True}
         for p in V1_PREFERENCES],
    )


def downgrade() -> None:
    set_id = _id(category_sets, version=1)
    cat_ids = sa.select(categories.c.id).where(categories.c.set_id == set_id)
    op.execute(category_examples.delete().where(category_examples.c.category_id.in_(cat_ids)))
    op.execute(categories.delete().where(categories.c.set_id == set_id))
    op.execute(category_flags.delete().where(category_flags.c.set_id == set_id))
    op.execute(category_sets.delete().where(category_sets.c.id == set_id))
    op.execute(kb_entries.delete().where(kb_entries.c.source == "seed"))
