import base64
import json
import time

from fastapi.testclient import TestClient

from src.api.app import create_app


def push_body(history_id="1"):
    data = json.dumps({"emailAddress": "me@x.com", "historyId": history_id})
    return {"message": {"data": base64.b64encode(data.encode()).decode()}}


def make_client(token=None, **kw):
    calls = []
    app = create_app(trigger=lambda: calls.append(1), token=token, **kw)
    return TestClient(app), calls


def test_valid_push_returns_204_and_triggers():
    client, calls = make_client()
    with client:
        r = client.post("/webhooks/gmail", json=push_body())
    assert r.status_code == 204
    assert calls == [1]


def test_malformed_body_returns_204_without_trigger():
    client, calls = make_client()
    bad_data = {"message": {"data": "%%%not-base64%%%"}}
    no_history = {
        "message": {"data": base64.b64encode(b'{"emailAddress":"a@b"}').decode()}
    }
    with client:
        assert client.post("/webhooks/gmail", content=b"not json").status_code == 204
        assert client.post("/webhooks/gmail", json=bad_data).status_code == 204
        assert client.post("/webhooks/gmail", json=no_history).status_code == 204
        assert client.post("/webhooks/gmail", json=[1, 2]).status_code == 204
    assert calls == []


def test_wrong_token_401():
    client, calls = make_client(token="s3cret")
    with client:
        assert client.post("/webhooks/gmail", json=push_body()).status_code == 401
        r = client.post("/webhooks/gmail?token=nope", json=push_body())
    assert r.status_code == 401
    assert calls == []


def test_right_token_204():
    client, calls = make_client(token="s3cret")
    with client:
        r = client.post("/webhooks/gmail?token=s3cret", json=push_body())
    assert r.status_code == 204
    assert calls == [1]


def test_no_token_configured_accepts_all():
    client, calls = make_client(token=None)
    with client:
        r = client.post("/webhooks/gmail?token=whatever", json=push_body())
    assert r.status_code == 204


def test_healthz():
    client, _ = make_client()
    with client:
        r = client.get("/healthz")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


def test_startup_failure_not_fatal():
    def boom():
        raise RuntimeError("sem token.json")

    client, _ = make_client(on_startup=boom)
    with client:
        assert client.get("/healthz").status_code == 200


def test_renew_loop_retries_after_failure():
    attempts = []

    def renew():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("falhou")

    client, _ = make_client(renew=renew, renew_interval_s=0.01, retry_s=0.01)
    with client:
        deadline = time.time() + 2
        while len(attempts) < 3 and time.time() < deadline:
            time.sleep(0.01)
    assert len(attempts) >= 3


def test_entrypoints_import():
    import src.api.app  # noqa: F401
    import src.cli.classify_unread  # noqa: F401
