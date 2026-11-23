import base64
from datetime import UTC, datetime, timedelta
from email import message_from_bytes

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.alerts.service import ALERT_HEADER, AlertService, FailureStreak, GmailMailSender
from src.db.models import Notification
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, ProcessEmailJob
from src.jobs.worker import Worker
from tests.agent_fakes import FakeLabels
from tests.fakes import FakeService


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t


class Sent:
    def __init__(self, error: Exception | None = None) -> None:
        self.mails: list[tuple[str, str]] = []
        self.error = error

    def send_to_self(self, subject: str, body: str) -> None:
        if self.error:
            raise self.error
        self.mails.append((subject, body))


def _service(sf: sessionmaker[Session], sent: Sent, clock: Clock) -> AlertService:
    return AlertService(sf, sent, clock=clock.now, account=lambda: 1)


def test_second_alert_within_15min_is_suppressed_then_counted(
    session_factory: sessionmaker[Session],
) -> None:
    sent, clock = Sent(), Clock()
    alerts = _service(session_factory, sent, clock)
    assert alerts.alert("job_dead", "job 1 morreu", "erro A") == "sent"
    clock.t += timedelta(minutes=5)
    assert alerts.alert("job_dead", "job 2 morreu", "erro B") == "suppressed"
    assert alerts.alert("sync_failures", "sync", "x") == "sent"  # other kind: independent
    clock.t += timedelta(minutes=11)
    assert alerts.alert("job_dead", "job 3 morreu", "erro C") == "sent"
    assert [s for s, _ in sent.mails] == ["job 1 morreu", "sync", "job 3 morreu"]
    assert "1 alerta(s) suprimido(s)" in sent.mails[2][1]
    with session_factory() as s:
        statuses = [n.status for n in s.scalars(select(Notification).order_by(Notification.id))]
    assert statuses == ["sent", "suppressed", "sent", "sent"]


def test_send_failure_never_raises(session_factory: sessionmaker[Session]) -> None:
    alerts = _service(session_factory, Sent(RuntimeError("gmail fora")), Clock())
    assert alerts.alert("job_dead", "x", "y") == "failed"
    with session_factory() as s:
        row = s.scalars(select(Notification)).one()
    assert row.status == "failed" and "gmail fora" in (row.error or "")


def test_failed_send_does_not_open_suppression_window(
    session_factory: sessionmaker[Session],
) -> None:
    clock = Clock()
    assert _service(session_factory, Sent(RuntimeError("x")), clock).alert("job_dead", "a", "b") == "failed"
    assert _service(session_factory, Sent(), clock).alert("job_dead", "a", "b") == "sent"


def test_alert_mail_has_header_prefix_and_label() -> None:
    labels = FakeLabels()
    svc = FakeService(messages__send=lambda **kw: {"id": "sent1"})
    GmailMailSender(lambda: svc, labels, lambda: "me@x.com").send_to_self("job morreu", "detalhes")
    ((name, kw),) = svc.calls
    assert name == "messages.send"
    body = kw["body"]
    assert isinstance(body, dict)
    mime = message_from_bytes(base64.urlsafe_b64decode(str(body["raw"])))
    assert mime["To"] == "me@x.com" and mime["From"] == "me@x.com"
    assert mime["Subject"] == "[gmail-integration] job morreu"
    assert mime[ALERT_HEADER] == "alert"
    assert labels.applied == [("sent1", ("Sistema/Alerta",))]


def test_sender_without_address_raises() -> None:
    with pytest.raises(RuntimeError):
        GmailMailSender(lambda: FakeService(), FakeLabels(), lambda: None).send_to_self("a", "b")


def test_failure_streak_trips_at_threshold_and_resets() -> None:
    trips: list[tuple[int, str]] = []
    streak = FailureStreak(3, lambda n, err: trips.append((n, err)))
    streak.failure("e1")
    streak.failure("e2")
    streak.success()
    streak.failure("e3")
    streak.failure("e4")
    assert trips == []
    streak.failure("e5")
    assert trips == [(3, "e5")]
    streak.failure("e6")
    assert trips == [(3, "e5"), (4, "e6")]


def test_three_failed_runs_trigger_agent_failures_alert(
    session_factory: sessionmaker[Session],
) -> None:
    sent = Sent()
    alerts = _service(session_factory, sent, Clock())
    streak = FailureStreak(
        3, lambda n, err: alerts.alert("agent_failures", f"{n} execuções falharam", err)
    )

    def broken(job: ClaimedJob) -> None:
        raise RuntimeError("gemini fora")

    jobs = JobStore(session_factory)
    for i in range(3):
        jobs.enqueue(ProcessEmailJob(email_id=f"e{i}"))
    worker = Worker(
        jobs, {"process_email": streak.guard(broken)}, on_dead=lambda j, e: None,
        poll_s=0, sleep=lambda _: None, worker_id="w", heartbeat=None,
    )
    for _ in range(3):
        worker.run_once()
    assert [s for s, _ in sent.mails] == ["3 execuções falharam"]
