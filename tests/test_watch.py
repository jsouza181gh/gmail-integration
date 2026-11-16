import pytest

from src.db.store import Store
from tests.fakes import FakeService
from src.gmail.watch import ensure_watch, start_gmail_watch

TOPIC = "projects/p/topics/t"


@pytest.fixture(autouse=True)
def topic(monkeypatch):
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)


def make_service(history_id="100", expiration="1000"):
    return FakeService(
        **{
            "users.watch": lambda **kw: {"historyId": history_id, "expiration": expiration},
            "users.getProfile": lambda **kw: {"emailAddress": "me@x.com"},
        }
    )


def test_watch_body():
    svc = make_service()
    resp = start_gmail_watch(svc)
    assert resp["historyId"] == "100"
    name, kw = svc.calls[0]
    assert name == "users.watch"
    assert kw["userId"] == "me"
    assert kw["body"] == {
        "topicName": TOPIC,
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }


def test_missing_topic_raises(monkeypatch):
    monkeypatch.delenv("GMAIL_PUBSUB_TOPIC")
    with pytest.raises(RuntimeError):
        start_gmail_watch(make_service())


def test_ensure_watch_first_run_saves_cursor(session_factory):
    store = Store(session_factory)
    ensure_watch(store, make_service("100", "1000"))
    assert store.get_watch_state() == {
        "id": 1,
        "email_address": "me@x.com",
        "history_id": "100",
        "expiration": 1000,
        "updated_at": store.get_watch_state()["updated_at"],
    }


def test_renew_does_not_move_cursor(session_factory):
    store = Store(session_factory)
    ensure_watch(store, make_service("100", "1000"))
    ensure_watch(store, make_service("999", "2000"))
    state = store.get_watch_state()
    assert state["history_id"] == "100"
    assert state["expiration"] == 2000
