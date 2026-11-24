"""Entry points open the right account scope: panel requests, alerts."""
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.alerts.service import AlertService
from src.api.deps import StreamTiming
from src.db.models import Email, Notification, User
from src.db.scope import account_scope, system_scope
from tests.api_fakes import Api

pytestmark = pytest.mark.noscope

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _mail(sf: sessionmaker[Session], account: int, email_id: str) -> None:
    with account_scope(account), sf.begin() as s:
        s.add(Email(id=email_id, subject=email_id, attempts=0, received_at=T0))


def test_request_runs_in_user_account(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    _mail(session_factory, 1, "mine")
    _mail(session_factory, second_account, "theirs")
    api = Api(session_factory)
    api.login()
    items = api.client.get("/api/emails").json()["items"]
    assert [e["id"] for e in items] == ["mine"]
    assert api.client.get("/api/emails/theirs").status_code == 404


def test_stream_keeps_scope(session_factory: sessionmaker[Session]) -> None:
    api = Api(session_factory, stream=StreamTiming(poll_s=0.01, max_s=0.2, ping_s=0.05))
    api.login()
    conv = api.client.post("/api/chat/conversations").json()["id"]
    turn = api.client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "oi"}).json()
    aid = turn["assistant"]["id"]
    with account_scope(1):
        api.deps.chat.begin(aid)
        api.deps.chat.finish(aid, "Olá!")
    body = api.client.get(f"/api/chat/messages/{aid}/stream").text
    assert "Olá!" in body


def test_unauthenticated_has_no_scope(session_factory: sessionmaker[Session]) -> None:
    api = Api(session_factory)
    assert api.client.get("/healthz").status_code == 200
    assert api.client.get("/api/emails").status_code == 401


class _Sent:
    def __init__(self) -> None:
        self.subjects: list[str] = []

    def send_to_self(self, subject: str, body: str) -> None:
        self.subjects.append(subject)


def test_alert_goes_to_admin_account(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    with system_scope(), session_factory.begin() as s:
        s.add(User(id=second_account, username="admin", password_hash="x", totp_secret_enc="x",
                   created_at=T0, password_changed_at=T0, role="admin"))
    sent = _Sent()
    assert AlertService(session_factory, sent).alert("job_dead", "x", "y") == "sent"
    with system_scope(), session_factory() as s:
        rows = s.query(Notification).all()
    assert [r.gmail_account_id for r in rows] == [second_account]
