"""pgvector behaviour. Runs when TEST_DATABASE_URL points at a migrated
database with the `vector` extension (CI does this)."""
import os

import pytest
from sqlalchemy import cast, select, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Email, EmailEmbedding
from src.db.session import make_session_factory
from src.db.vector import EMBED_DIM, Vector

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")


def unit(i: int) -> tuple[float, ...]:
    return tuple(1.0 if j == i else 0.0 for j in range(EMBED_DIM))


@pytest.fixture
def pg() -> sessionmaker[Session]:
    sf = make_session_factory(URL)
    with sf.begin() as s:
        s.execute(text("DELETE FROM email_embeddings WHERE email_id LIKE 'vectest-%'"))
        s.execute(text("DELETE FROM emails WHERE id LIKE 'vectest-%'"))
        for i in range(2):
            s.add(Email(id=f"vectest-{i}", attempts=0))
        s.flush()
        for i in range(2):
            s.add(EmailEmbedding(email_id=f"vectest-{i}", model="m", embedding=unit(i)))
    return sf


def test_vector_roundtrip_and_nearest_first(pg: sessionmaker[Session]) -> None:
    query = tuple(0.9 if j == 1 else (0.1 if j == 0 else 0.0) for j in range(EMBED_DIM))
    with pg() as s:
        assert s.get_one(EmailEmbedding, "vectest-0").embedding == unit(0)
        ids = s.scalars(
            select(EmailEmbedding.email_id)
            .where(EmailEmbedding.email_id.like("vectest-%"))
            .order_by(EmailEmbedding.embedding.op("<=>")(cast(query, Vector())))
        ).all()
    assert ids == ["vectest-1", "vectest-0"]


def test_hnsw_indexes_exist(pg: sessionmaker[Session]) -> None:
    with pg() as s:
        names = set(s.scalars(text("SELECT indexname FROM pg_indexes WHERE indexdef ILIKE '%hnsw%'")))
    assert {"ix_email_embeddings_hnsw", "ix_attachments_hnsw"} <= names
