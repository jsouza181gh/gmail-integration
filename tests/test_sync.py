import threading
import time

import pytest

from src.db.store import Store
from src.jobs.store import JobStore
from src.services.sync import WEBHOOK_QUERY, SyncCoordinator, SyncResult, sync_inbox
from tests.fakes import FakeService, http_error


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


def make_service(history=None, gone=(), system=(), **extra):
    def get(userId, id, **kw):
        if id in gone:
            raise http_error(404)
        msg = message(id)
        if id in system:
            msg["payload"]["headers"].append({"name": "X-Gmail-Integration", "value": "alert"})
        return msg

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


@pytest.fixture
def jobs(session_factory):
    return JobStore(session_factory)


def queued(jobs):
    out = []
    while (job := jobs.claim("t")) is not None:
        out.append(job.payload.email_id)
    return out


def run_of(store):
    return store.get_or_create_run(WEBHOOK_QUERY).id


def test_ingests_and_enqueues_new_messages(store, jobs):
    svc = make_service(lambda **kw: history_response(["m1", "m2"], "20"))
    result = sync_inbox(store, svc, jobs)

    assert result == SyncResult(ingested=2, enqueued=2)
    assert queued(jobs) == ["m1", "m2"]
    rows = store.report_rows(run_of(store))
    assert {r.id for r in rows} == {"m1", "m2"}
    assert all(r.category is None for r in rows)  # classification is the worker's job
    assert rows[0].sender.endswith("@x.com")
    assert rows[0].received_at is not None
    assert store.get_watch_state().history_id == "20"
    name, kw = svc.calls[0]
    assert (name, kw["startHistoryId"], kw["labelId"]) == ("history.list", "10", "INBOX")
    assert kw["historyTypes"] == ["messageAdded"]


def test_duplicate_notification_no_duplicates(store, jobs):
    svc = make_service(lambda **kw: history_response(["m1"], "20"))
    sync_inbox(store, svc, jobs)
    second = sync_inbox(store, svc, jobs)

    assert second == SyncResult(ingested=0, enqueued=0)
    assert len(store.report_rows(run_of(store))) == 1
    assert queued(jobs) == ["m1"]


def test_deleted_message_is_skipped(store, jobs):
    svc = make_service(
        lambda **kw: history_response(["m1", "m2"], "20"), gone={"m2"}
    )
    result = sync_inbox(store, svc, jobs)
    assert result.ingested == 1
    assert {r.id for r in store.report_rows(run_of(store))} == {"m1"}
    assert queued(jobs) == ["m1"]
    assert store.get_watch_state().history_id == "20"


def test_history_expired_falls_back(store, jobs):
    def expired(**kw):
        raise http_error(404)

    svc = make_service(
        expired,
        messages__list=lambda **kw: {"messages": [{"id": "m1"}]},
        users__getProfile=lambda **kw: {"historyId": "500"},
    )
    result = sync_inbox(store, svc, jobs)

    assert result.ingested == 1
    assert store.get_watch_state().history_id == "500"
    listing = [kw for name, kw in svc.calls if name == "messages.list"][0]
    assert listing["q"] == "in:inbox newer_than:7d"


def test_sync_without_watch_state_raises(session_factory):
    with pytest.raises(RuntimeError):
        sync_inbox(Store(session_factory), make_service(), JobStore(session_factory))


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


def test_sync_ignores_system_alert_messages(store, jobs):
    svc = make_service(lambda **kw: history_response(["m1", "alert1"], "20"), system={"alert1"})
    result = sync_inbox(store, svc, jobs)
    assert result == SyncResult(ingested=1, enqueued=1)
    assert queued(jobs) == ["m1"]
    assert {r.id for r in store.report_rows(run_of(store))} == {"m1"}
