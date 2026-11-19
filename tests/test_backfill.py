from collections.abc import Mapping, Sequence
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.classification.taxonomy import Classification
from src.db.models import AgentRun, Email, Job, Run, SuggestedAction
from src.db.rows import EmailRow
from src.db.store import Store
from src.indexing.backfill import BACKFILL_QUERY, PAGE_SIZE, Backfill
from src.indexing.indexer import Indexer
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.jobs.types import BackfillPageJob, ClaimedJob
from src.jobs.worker import Deferred
from src.llm.errors import LLMExhaustedError
from tests.fakes import FakeService
from tests.gmail_samples import full_message
from tests.test_indexer import FakeEmbedder


class FakeClassifier:
    def __init__(self, fail: Exception | None = None, skip: Sequence[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self.bodies: list[Mapping[str, str]] = []
        self.fail = fail
        self.skip = set(skip)

    def classify(self, rows: Sequence[EmailRow], bodies: Mapping[str, str] | None = None
                 ) -> dict[str, Classification]:
        self.calls.append([r.id for r in rows])
        self.bodies.append(dict(bodies or {}))
        if self.fail is not None:
            raise self.fail
        return {
            r.id: Classification(category="Social", reason="r", is_relevant=False,
                                 deadline=date(2026, 12, 1), has_document=False, ephemeral=False,
                                 category_set_id=1)
            for r in rows if r.id not in self.skip
        }


class FakeAlerts:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def alert(self, kind: str, subject: str, body: str) -> str:
        self.sent.append((kind, subject))
        return "sent"


def gmail(pages: list[list[str]], lists: list[dict[str, object]] | None = None) -> FakeService:
    def list_(**kw: object) -> object:
        if lists is not None:
            lists.append(dict(kw))
        token = kw.get("pageToken")
        n = 0 if token is None else int(str(token).removeprefix("p"))
        resp: dict[str, object] = {"messages": [{"id": i} for i in pages[n]]}
        if n + 1 < len(pages):
            resp["nextPageToken"] = f"p{n + 1}"
        return resp

    return FakeService(messages__list=list_,
                       messages__get=lambda **kw: full_message(str(kw["id"]), body=f"corpo {kw['id']}"))


class World:
    def __init__(self, sf: sessionmaker[Session], pages: list[list[str]],
                 classifier: FakeClassifier | None = None) -> None:
        self.sf = sf
        self.lists: list[dict[str, object]] = []
        self.svc = gmail(pages, self.lists)
        self.store = Store(sf)
        self.jobs = JobStore(sf)
        self.index_store = IndexStore(sf)
        self.classifier = classifier or FakeClassifier()
        self.alerts = FakeAlerts()
        self.indexer = Indexer(self.index_store, self.jobs, FakeEmbedder(), lambda: self.svc)
        self.backfill = Backfill(self.store, self.index_store, self.indexer, self.classifier,
                                 self.jobs, self.alerts, lambda: self.svc)

    def drain(self, limit: int = 20) -> list[BackfillPageJob]:
        """Run queued backfill jobs in order; other kinds are left alone."""
        done: list[BackfillPageJob] = []
        for _ in range(limit):
            with self.sf() as s:
                row = s.scalars(select(Job).where(Job.kind == "backfill_page", Job.status == "queued")
                                .order_by(Job.id)).first()
            if row is None:
                return done
            job = self.jobs.claim("w")
            assert job is not None and isinstance(job.payload, BackfillPageJob)
            self.backfill.handle(job)
            self.jobs.complete(job.id)
            done.append(job.payload)
        return done


def test_two_pages_chain_and_finish(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a", "b"], ["c"]])
    w.backfill.start()
    assert [p.page_no for p in w.drain()] == [0, 1]
    run = w.store.get_run(w.backfill.run().id)
    assert (run.pages, run.fetch_done) == (2, True)
    assert w.classifier.calls == [["a", "b"], ["c"]]
    assert w.classifier.bodies[0] == {"a": "corpo a", "b": "corpo b"}
    assert w.alerts.sent == [("backfill_done", w.alerts.sent[0][1])]
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Email).where(Email.category == "Social")) == 3
    first = w.lists[0]
    assert (first["q"], first["maxResults"], first["includeSpamTrash"]) == (BACKFILL_QUERY, PAGE_SIZE, False)
    assert "pageToken" not in first and w.lists[1]["pageToken"] == "p1"


def test_crash_after_save_page_heals_chain(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a"], ["b"]])
    w.backfill.start()
    job = w.jobs.claim("w")
    assert job is not None
    real_enqueue = w.jobs.enqueue
    w.jobs.enqueue = lambda payload: False  # type: ignore[method-assign]  # the deploy kills us here
    w.backfill.handle(job)
    w.jobs.enqueue = real_enqueue  # type: ignore[method-assign]
    assert w.store.get_run(w.backfill.run().id).pages == 1
    # the worker restarts: orphan requeue, the same page job runs again
    w.jobs.requeue_orphans()
    listed = len(w.lists)
    assert [p.page_no for p in w.drain()] == [0, 1]
    assert len(w.lists) == listed + 1  # page 0 was not listed again
    assert w.store.get_run(w.backfill.run().id).fetch_done


def test_retry_of_same_page_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a", "b"]], FakeClassifier(fail=RuntimeError("503")))
    w.backfill.start()
    job = w.jobs.claim("w")
    assert job is not None
    with pytest.raises(RuntimeError):
        w.backfill.handle(job)
    assert w.store.get_run(w.backfill.run().id).pages == 0  # cursor did not move
    w.classifier.fail = None
    w.backfill.handle(job)
    run = w.store.get_run(w.backfill.run().id)
    assert (run.pages, run.fetch_done) == (1, True)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Email)) == 2


def test_unanswered_emails_are_retried_then_given_up(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a", "b"]], FakeClassifier(skip=["b"]))
    w.backfill.start()
    w.drain()
    assert w.classifier.calls[0] == ["a", "b"] and all(c == ["b"] for c in w.classifier.calls[1:])
    with session_factory() as s:
        assert s.get_one(Email, "b").category == "Não classificado"


def test_quota_defers_page(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a"]], FakeClassifier(fail=LLMExhaustedError("429", kinds=("rate",))))
    w.backfill.start()
    job = w.jobs.claim("w")
    assert job is not None
    with pytest.raises(Deferred):
        w.backfill.handle(job)


def test_backfill_never_proposes_actions(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a", "b"]])
    w.backfill.start()
    w.drain()
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(SuggestedAction)) == 0
        assert s.scalar(select(func.count()).select_from(AgentRun)) == 0
        kinds = set(s.scalars(select(Job.kind)))
    assert kinds == {"backfill_page"}


def test_start_is_idempotent_and_reuses_the_run(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a"]])
    first = w.backfill.start()
    second = w.backfill.start()
    assert first.run_id == second.run_id
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 1
    w.drain()
    w.backfill.start()  # finished: nothing new, same run
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 1
        assert s.scalar(select(func.count()).select_from(Job)) == 1


def test_stray_job_for_future_page_is_ignored(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a"], ["b"]])
    run_id = w.backfill.start().run_id
    w.backfill.handle(ClaimedJob(id=99, attempts=1, payload=BackfillPageJob(run_id=run_id, page_no=5)))
    assert w.lists == []


def test_progress_counts(session_factory: sessionmaker[Session]) -> None:
    w = World(session_factory, [["a", "b"]])
    w.backfill.start()
    w.drain()
    p = w.backfill.progress()
    assert (p.pages, p.fetch_done, p.emails, p.classified, p.indexed, p.embedded) == (1, True, 2, 2, 2, 2)
    assert p.jobs["backfill_page"].done == 1


def test_cli_render(session_factory: sessionmaker[Session]) -> None:
    from src.cli.backfill import render

    w = World(session_factory, [["a"]])
    w.backfill.start()
    w.drain()
    out = render(w.backfill.progress())
    assert "concluído" in out and "classificados: 1" in out and "backfill_page" in out
