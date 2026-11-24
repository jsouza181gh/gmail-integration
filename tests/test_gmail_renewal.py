"""Watch renewal over connected accounts, per-account sync coordination, revoked alert."""
import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.alerts.service import AlertService, gmail_revoked_hook
from src.db.models import GmailAccount, Notification
from src.db.scope import account_scope, system_scope
from src.gmail.accounts_store import mark_status
from src.gmail.watch import renew_due
from src.services.sync import SyncCoordinators
from tests.conftest import add_account
from tests.oauth_fakes import app_secret, connect  # noqa: F401

DAY_MS = 86_400_000
NOW = 1_000 * DAY_MS


def give_watch(sf: sessionmaker[Session], account_id: int, expiration: int | None) -> None:
    with system_scope(), sf.begin() as s:
        s.get_one(GmailAccount, account_id).watch_expiration = expiration


def test_renews_only_accounts_expiring_within_two_days(
    session_factory: sessionmaker[Session],
) -> None:
    for i, (email, exp) in enumerate(
        [("a@x", NOW + DAY_MS), ("b@x", NOW + 9 * DAY_MS), ("c@x", NOW + 2 * DAY_MS - 1)], start=2
    ):
        add_account(session_factory, i, email)
        connect(session_factory, i, email)
        give_watch(session_factory, i, exp)
    renewed: list[int] = []
    assert renew_due(session_factory, NOW, renewed.append, 2 * DAY_MS) == []
    assert renewed == [2, 4]


def test_one_failing_account_does_not_stop_the_others(
    session_factory: sessionmaker[Session],
) -> None:
    for i, email in enumerate(["a@x", "b@x", "c@x"], start=2):
        add_account(session_factory, i, email)
        connect(session_factory, i, email)
    done: list[int] = []

    def renew(account: int) -> None:
        if account == 3:
            raise RuntimeError("gmail fora do ar")
        done.append(account)

    errors = renew_due(session_factory, NOW, renew, 2 * DAY_MS)
    assert done == [2, 4]
    assert len(errors) == 1 and "conta 3" in errors[0] and "RuntimeError" in errors[0]


def test_coordinators_run_per_account_and_coalesce_per_account() -> None:
    started, release = threading.Event(), threading.Event()
    runs: list[int] = []

    def run_for(account: int) -> None:
        runs.append(account)
        if account == 1 and runs.count(1) == 1:
            started.set()
            release.wait(2)

    syncs = SyncCoordinators(run_for)
    first = threading.Thread(target=syncs.trigger, args=(1,))
    first.start()
    assert started.wait(2)
    syncs.trigger(1)  # coalesces into one extra run of account 1
    syncs.trigger(1)
    syncs.trigger(2)  # not blocked by account 1's run
    assert runs == [1, 2]
    release.set()
    first.join(2)
    time.sleep(0.05)
    assert sorted(runs) == [1, 1, 2]


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_to_self(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


@pytest.mark.noscope
def test_revoked_alert_goes_to_admin_without_content(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    from src.db.models import User

    with system_scope(), session_factory.begin() as s:
        s.add(User(id=1, username="adm", email="me@example.com", role="admin", password_hash="x",
                   totp_secret_enc="x", created_at=datetime.now(UTC),
                   password_changed_at=datetime.now(UTC)))
    sender = Sender()
    alerts = AlertService(session_factory, sender)
    gmail_revoked_hook(alerts)(second_account, "other@example.com")
    assert len(sender.sent) == 1
    subject, body = sender.sent[0]
    assert "other@example.com" in subject and "invalid_grant" in body
    with account_scope(1), session_factory() as s:
        kinds = [n.kind for n in s.query(Notification)]
    assert kinds == ["gmail_revoked"]
    mark_status(session_factory, second_account, "revoked", None)  # status helper still works
