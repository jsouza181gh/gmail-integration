import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import ActionsDraft, RuleDraft
from src.api.deps import StreamTiming
from src.db.models import AgentConfig, AutomationRule, Job
from tests.api_fakes import ORIGIN, Api
from tests.chat_fakes import add_email, wav


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    with session_factory.begin() as s:
        s.add(AgentConfig(version=1, active=True, decide_prompt="d", chat_prompt="c", max_turns=4))
    a = Api(session_factory, stream=StreamTiming(poll_s=0.01, max_s=0.2, ping_s=0.05))
    a.login()
    return a


def _conv(api: Api) -> int:
    r = api.client.post("/api/chat/conversations")
    assert r.status_code == 200, r.text
    conv_id: int = r.json()["id"]
    return conv_id


def _jobs(api: Api) -> list[tuple[str, int | None]]:
    with api.sf() as s:
        return [(j.kind, j.chat_message_id) for j in s.scalars(select(Job).order_by(Job.id))]


def _frames(text: str) -> list[dict[str, object]]:
    out = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: turn":
            out.append(json.loads(lines[1].removeprefix("data: ")))
    return out


def test_text_turn_enqueues_chat_job(api: Api) -> None:
    conv = _conv(api)
    r = api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "oi"})
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["user"]["text"] == "oi" and turn["assistant"]["status"] == "queued"
    assert _jobs(api) == [("chat_turn", turn["assistant"]["id"])]
    msgs = api.client.get(f"/api/chat/conversations/{conv}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    (row,) = api.client.get("/api/chat/conversations").json()
    assert row["busy"] is True and row["title"] == "oi"


def test_busy_conversation_409(api: Api) -> None:
    conv = _conv(api)
    api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "oi"})
    r = api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "de novo"})
    assert r.status_code == 409
    assert api.client.post("/api/chat/conversations/999/messages",
                           json={"text": "x"}).status_code == 404
    assert api.client.post(f"/api/chat/conversations/{_conv(api)}/messages",
                           json={"text": "  "}).status_code == 422


def test_audio_upload_validation(api: Api) -> None:
    conv = _conv(api)
    url = f"/api/chat/conversations/{conv}/audio"
    post = api.client.post
    assert post(url, content=wav(1), headers={"Content-Type": "audio/ogg"}).status_code == 415
    big = b"RIFF" + b"\x00" * (4 * 1024 * 1024 + 1)
    assert post(url, content=big, headers={"Content-Type": "audio/wav"}).status_code == 413
    stereo = post(url, content=wav(1, channels=2), headers={"Content-Type": "audio/wav"})
    assert stereo.status_code == 422 and "mono" in stereo.json()["detail"]
    ok = post(url, content=wav(2), headers={"Content-Type": "audio/wav"})
    assert ok.status_code == 200, ok.text
    user = ok.json()["user"]
    assert user["input"] == "audio" and user["audio_ms"] == 2000
    assert _jobs(api) == [("chat_turn", ok.json()["assistant"]["id"])]
    got = api.client.get(f"/api/chat/messages/{user['id']}/audio")
    assert got.status_code == 200 and got.content == wav(2)
    assert got.headers["content-type"] == "audio/wav"


def test_audio_playback_requires_auth(api: Api) -> None:
    conv = _conv(api)
    ok = api.client.post(f"/api/chat/conversations/{conv}/audio", content=wav(1),
                         headers={"Content-Type": "audio/wav"})
    api.client.cookies.clear()
    r = api.client.get(f"/api/chat/messages/{ok.json()['user']['id']}/audio")
    assert r.status_code == 401


def test_retry_only_failed(api: Api) -> None:
    conv = _conv(api)
    turn = api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "oi"}).json()
    aid = turn["assistant"]["id"]
    assert api.client.post(f"/api/chat/messages/{aid}/retry").status_code == 409
    api.deps.chat.begin(aid)
    api.deps.chat.fail(aid, "O Gemini está sobrecarregado agora.")
    r = api.client.post(f"/api/chat/messages/{aid}/retry")
    assert r.status_code == 200 and r.json()["assistant"]["id"] != aid
    assert [k for k, _ in _jobs(api)] == ["chat_turn", "chat_turn"]


def test_stream_ends_on_done_and_on_timeout(api: Api) -> None:
    conv = _conv(api)
    turn = api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "oi"}).json()
    aid = turn["assistant"]["id"]
    with api.client.stream("GET", f"/api/chat/messages/{aid}/stream") as r:
        assert r.status_code == 200
        assert r.headers["x-accel-buffering"] == "no"
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())  # never finishes: closes at max_s
    frames = _frames(body)
    assert len(frames) == 1 and ": ping" in body
    api.deps.chat.begin(aid)
    api.deps.chat.finish(aid, "Olá!")
    body = api.client.get(f"/api/chat/messages/{aid}/stream").text
    (last,) = _frames(body)
    assistant = last["assistant"]
    assert isinstance(assistant, dict) and assistant["status"] == "done"
    assert assistant["text"] == "Olá!"
    assert api.client.get("/api/chat/messages/999/stream").status_code == 404


def test_confirm_and_cancel_cards_csrf_protected(api: Api) -> None:
    add_email(api.sf, "e1")
    conv = _conv(api)
    aid = api.client.post(f"/api/chat/conversations/{conv}/messages",
                          json={"text": "regra"}).json()["assistant"]["id"]
    card = api.deps.chat.add_card(aid, "chat", RuleDraft(
        name="lixo", action="trash", sender_domain="loja.com", affected=1))
    other = api.deps.chat.add_card(aid, "chat", RuleDraft(
        name="outra", action="archive", sender_domain="loja.com", affected=1))
    token = api.client.headers.pop("X-CSRF-Token")
    assert api.client.post(f"/api/chat/cards/{card}/confirm", json={}).status_code == 403
    api.client.headers["X-CSRF-Token"] = token
    r = api.client.post(f"/api/chat/cards/{card}/confirm", json={"apply_existing": True})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["apply_existing"] is True
    c = api.client.post(f"/api/chat/cards/{other}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    with api.sf() as s:
        assert [r.name for r in s.scalars(select(AutomationRule))] == ["lixo"]
    assert api.client.headers["Origin"] == ORIGIN


def test_confirm_twice_409(api: Api) -> None:
    conv = _conv(api)
    aid = api.client.post(f"/api/chat/conversations/{conv}/messages",
                          json={"text": "x"}).json()["assistant"]["id"]
    card = api.deps.chat.add_card(aid, "chat", RuleDraft(
        name="lixo", action="trash", sender_domain="loja.com", affected=0))
    assert api.client.post(f"/api/chat/cards/{card}/confirm", json={}).status_code == 200
    assert api.client.post(f"/api/chat/cards/{card}/confirm", json={}).status_code == 409
    assert api.client.post("/api/chat/cards/999/confirm", json={}).status_code == 404
    actions = api.deps.chat.add_card(aid, "chat", ActionsDraft(action_ids=()))
    assert api.client.post(f"/api/chat/cards/{actions}/confirm", json={}).status_code == 422


def test_new_config_keeps_chat_prompt(api: Api) -> None:
    c = api.client
    r = c.post("/api/agent/config", json={"decide_prompt": "v2", "max_turns": 3})
    assert r.status_code == 200 and r.json()["chat_prompt"] == "c"
    r = c.post("/api/agent/config", json={"decide_prompt": "v3", "max_turns": 3,
                                          "chat_prompt": "novo chat"})
    assert r.json()["chat_prompt"] == "novo chat"


def test_add_rule_unless_document(api: Api) -> None:
    r = api.client.post("/api/agent/rules", json={
        "name": "lixo", "action": "trash", "sender_domain": "loja.com", "unless_document": True,
    })
    assert r.status_code == 200 and r.json()["unless_document"] is True
