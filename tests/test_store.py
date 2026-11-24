from datetime import date

import pytest

from src.db.scope import email_key
from src.classification.classifier import Classification
from src.db.store import Store
from src.gmail.messages import MessageSummary


@pytest.fixture
def store(session_factory):
    return Store(session_factory)


def _email(i):
    return MessageSummary(id=i, sender=f"{i}@x", subject=f"s{i}", date="d", snippet="sn")


def test_save_page_idempotent(store):
    run = store.get_or_create_run("q")
    page = (run.id, ["a", "b"], [_email("a"), _email("b")])
    store.save_page(*page, "tok")
    store.save_page(*page, None)

    assert store.count_pending(run.id) == 2
    got = store.get_run(run.id)
    assert got.pages == 2
    assert got.fetch_done is True
    assert store.known_ids(["a", "b", "z"]) == {"a", "b"}


def test_get_or_create_run_resumes_unfinished(store):
    first = store.get_or_create_run("q")
    again = store.get_or_create_run("q")
    assert again.id == first.id
    assert store.get_or_create_run("q", force_new=True).id != first.id


def test_pending_orders_by_attempts(store):
    run = store.get_or_create_run("q")
    store.ingest(run.id, ["a", "b"], [_email("a"), _email("b")])
    store.bump_attempts(["a"], "boom")
    assert [r.id for r in store.pending(run.id, 10)] == ["b", "a"]
    assert store.pending(run.id, 1)[0].id == "b"


def test_save_classifications_clears_pending(store):
    run = store.get_or_create_run("q")
    store.ingest(run.id, ["a"], [_email("a")])
    store.bump_attempts(["a"], "boom")
    store.save_classifications({"a": Classification(
        category="Estudos", reason="porque", is_relevant=True, deadline=date(2026, 11, 29),
        has_document=False, ephemeral=True, category_set_id=7,
    )})
    assert store.count_pending(run.id) == 0
    row = store.report_rows(run.id)[0]
    assert (row.category, row.reason, row.sender) == ("Estudos", "porque", "a@x")
    assert (row.is_relevant, row.deadline, row.has_document, row.ephemeral) == (
        True, date(2026, 11, 29), False, True,
    )
    assert row.category_set_id == 7 and row.last_error is None


def test_give_up_marks_nao_classificado(store):
    run = store.get_or_create_run("q")
    store.ingest(run.id, ["a", "b"], [_email("a"), _email("b")])
    for _ in range(4):
        store.bump_attempts(["a"], "err")
    store.give_up(4, "Não classificado", "modelo não respondeu")
    rows = {r.id: r for r in store.report_rows(run.id)}
    assert rows["a"].category == "Não classificado"
    assert rows["b"].category is None


def test_reset_cursor(store):
    run = store.get_or_create_run("q")
    store.save_page(run.id, [], [], "tok")
    assert store.get_run(run.id).page_token == "tok"
    store.reset_cursor(run.id)
    assert store.get_run(run.id).page_token is None


def test_init_watch_state_keeps_history_id(store):
    assert store.get_watch_state() is None
    store.init_watch_state("a@b", "100", 1)
    store.init_watch_state("a@b", "999", 2)
    state = store.get_watch_state()
    assert state.history_id == "100"
    assert state.expiration == 2


def test_set_history_id(store):
    store.init_watch_state("a@b", "100", 1)
    store.set_history_id("150")
    assert store.get_watch_state().history_id == "150"


def test_ingest_promotes_chat_row_to_inbox(store):
    from src.db.models import Email

    with store.sf.begin() as s:
        s.add(Email(id="sp", sender="spam@x", subject="oferta", attempts=0, source="chat"))
    run = store.get_or_create_run("q")
    store.ingest(run.id, ["sp"], [])

    with store.sf() as s:
        row = s.get(Email, email_key("sp"))
        assert row is not None
        assert (row.source, row.sender, row.subject) == ("inbox", "spam@x", "oferta")
