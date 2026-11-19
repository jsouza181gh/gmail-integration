import threading
import time

import pytest

from src.classification.classifier import Abort, Classification
from src.db.store import Store
from src.services.sync import WEBHOOK_QUERY, SyncCoordinator, sync_inbox
from tests.fakes import FakeService, http_error


class FakeClassifier:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def classify(self, rows):
        self.calls += 1
        if self.error:
            raise self.error
        return {
            r.id: Classification(
                category="Social", reason="teste", is_relevant=False, deadline=None,
                has_document=False, ephemeral=False, category_set_id=1,
            )
            for r in rows
        }


def message(mid):
    return {
        "id": mid,
        "snippet": f"snip {mid}",
        "payload": {
            "headers": [
                {"name": "From", "value": f"{mid}@x.com"},
                {"name": "Subject", "value": f"assunto {mid}"},
                {"name": "Date", "value": "Mon, 16 Nov 2026 10:00:00 +0000"},
            ]
        },
    }


def history_response(ids, latest):
    resp = {"historyId": latest}
    if ids:
        resp["history"] = [{"messagesAdded": [{"message": {"id": i}} for i in ids]}]
    return resp


def make_service(history=None, gone=(), **extra):
    def get(userId, id, **kw):
        if id in gone:
            raise http_error(404)
        return message(id)

    handlers = {
        "history.list": history or (lambda **kw: history_response([], "10")),
        "messages.get": get,
    }
    handlers.update({k.replace("__", "."): v for k, v in extra.items()})
    return FakeService(**handlers)


@pytest.fixture
def store(session_factory):
    s = Store(session_factory)
    s.init_watch_state("me@x.com", "10", 1)
    return s


def run_of(store):
    return store.get_or_create_run(WEBHOOK_QUERY).id


def test_ingests_and_classifies_new_message(store):
    svc = make_service(lambda **kw: history_response(["m1", "m2"], "20"))
    result = sync_inbox(store, svc, FakeClassifier())

    assert result == {"ingested": 2, "pending": 0}
    rows = store.report_rows(run_of(store))
    assert {r.id for r in rows} == {"m1", "m2"}
    assert all(r.category == "Social" for r in rows)
    assert rows[0].sender.endswith("@x.com")
    assert rows[0].received_at is not None
    assert store.get_watch_state().history_id == "20"
    name, kw = svc.calls[0]
    assert (name, kw["startHistoryId"], kw["labelId"]) == ("history.list", "10", "INBOX")
    assert kw["historyTypes"] == ["messageAdded"]


def test_duplicate_notification_no_duplicates(store):
    svc = make_service(lambda **kw: history_response(["m1"], "20"))
    clf = FakeClassifier()
    sync_inbox(store, svc, clf)
    second = sync_inbox(store, svc, clf)

    assert second == {"ingested": 0, "pending": 0}
    assert len(store.report_rows(run_of(store))) == 1
    assert clf.calls == 1


def test_deleted_message_is_skipped(store):
    svc = make_service(
        lambda **kw: history_response(["m1", "m2"], "20"), gone={"m2"}
    )
    result = sync_inbox(store, svc, FakeClassifier())
    assert result["ingested"] == 1
    assert {r.id for r in store.report_rows(run_of(store))} == {"m1"}
    assert store.get_watch_state().history_id == "20"


def test_history_expired_falls_back(store):
    def expired(**kw):
        raise http_error(404)

    svc = make_service(
        expired,
        messages__list=lambda **kw: {"messages": [{"id": "m1"}]},
        users__getProfile=lambda **kw: {"historyId": "500"},
    )
    result = sync_inbox(store, svc, FakeClassifier())

    assert result["ingested"] == 1
    assert store.get_watch_state().history_id == "500"
    listing = [kw for name, kw in svc.calls if name == "messages.list"][0]
    assert listing["q"] == "in:inbox newer_than:7d"


def test_llm_abort_keeps_pending_and_advances_cursor(store):
    svc = make_service(lambda **kw: history_response(["m1"], "20"))
    first = sync_inbox(store, svc, FakeClassifier(error=Abort("llm fora")))
    assert first == {"ingested": 1, "pending": 1}
    assert store.get_watch_state().history_id == "20"

    empty = make_service(lambda **kw: history_response([], "20"))
    second = sync_inbox(store, empty, FakeClassifier())
    assert second == {"ingested": 0, "pending": 0}
    assert store.report_rows(run_of(store))[0].category == "Social"


def test_sync_without_watch_state_raises(session_factory):
    with pytest.raises(RuntimeError):
        sync_inbox(Store(session_factory), make_service(), FakeClassifier())


def test_coordinator_coalesces_and_never_runs_in_parallel():
    started, release = threading.Event(), threading.Event()
    state = {"runs": 0, "active": 0, "max_active": 0}

    def run_fn():
        state["runs"] += 1
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        started.set()
        release.wait(2)
        state["active"] -= 1

    coord = SyncCoordinator(run_fn)
    worker = threading.Thread(target=coord.trigger)
    worker.start()
    assert started.wait(2)

    coord.trigger()  # while running: just marks dirty, returns immediately
    coord.trigger()
    release.set()
    worker.join(2)
    time.sleep(0.05)

    assert state["runs"] == 2
    assert state["max_active"] == 1


def test_coordinator_survives_run_fn_error():
    calls = []

    def run_fn():
        calls.append(1)
        raise ValueError("boom")

    coord = SyncCoordinator(run_fn)
    coord.trigger()
    coord.trigger()
    assert len(calls) == 2
