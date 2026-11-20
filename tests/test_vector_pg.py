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
        s.execute(text("DELETE FROM attachments WHERE email_id LIKE 'vectest-%'"))
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


def test_search_ranks_nearest_first(pg: sessionmaker[Session]) -> None:
    from src.db.models import Attachment
    from src.indexing.store import IndexStore

    with pg.begin() as s:
        s.execute(text("DELETE FROM attachments WHERE email_id LIKE 'vectest-%'"))
        s.add(Attachment(email_id="vectest-0", part_id="1", filename="b.pdf", mime_type="application/pdf",
                         size=1, status="done", title="Boleto", embedding=unit(2)))
    near_att = tuple(1.0 if j == 2 else 0.0 for j in range(EMBED_DIM))
    hits = IndexStore(pg).search(near_att, k=50)
    mine = [h for h in hits if h.id in ("vectest-0", "vectest-1") or h.title == "Boleto"]
    assert mine[0].kind == "attachment" and mine[0].title == "Boleto"
    assert mine[0].score > mine[-1].score


def test_retriever_merges_vector_and_keyword_pg(pg: sessionmaker[Session]) -> None:
    from collections.abc import Sequence

    from src.chat.retrieval import DbRetriever
    from src.indexing.store import IndexStore
    from src.llm.types import CallContext, EmbedInput, EmbedResult, EmbedTask

    class OneVector:
        def embed(
            self,
            inputs: Sequence[EmbedInput],
            *,
            purpose: str,
            task: EmbedTask,
            context: CallContext = CallContext(),
        ) -> EmbedResult:
            assert task == "RETRIEVAL_QUERY" and purpose == "chat_search"
            return EmbedResult(vectors=(unit(1),), model="m")

    with pg.begin() as s:
        s.get_one(Email, "vectest-0").subject = "Boleto vectestzz"
    hits = DbRetriever(IndexStore(pg), OneVector()).search("vectestzz", 2, CallContext())
    ids = [h.id for h in hits]
    assert ids[0] == "vectest-1" and hits[0].score > 0.99
    assert "vectest-0" in ids


def test_retriever_falls_back_to_keyword_when_embedding_fails_pg(pg: sessionmaker[Session]) -> None:
    from collections.abc import Sequence

    from src.chat.retrieval import DbRetriever
    from src.indexing.store import IndexStore
    from src.llm.errors import LLMExhaustedError
    from src.llm.types import CallContext, EmbedInput, EmbedResult, EmbedTask

    class QuotaGone:
        def embed(
            self,
            inputs: Sequence[EmbedInput],
            *,
            purpose: str,
            task: EmbedTask,
            context: CallContext = CallContext(),
        ) -> EmbedResult:
            raise LLMExhaustedError("cota esgotada", kinds=("rate",))

    with pg.begin() as s:
        s.get_one(Email, "vectest-0").subject = "Boleto vectestyy"
    hits = DbRetriever(IndexStore(pg), QuotaGone()).search("vectestyy", 2, CallContext())
    assert [h.id for h in hits] == ["vectest-0"]
