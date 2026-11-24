"""Alerts by email, sent by the server to its own Gmail address.

System code, not the agent: no approval queue, fixed content. At most one
email per kind every 15 minutes; the rest are logged as `suppressed` and
counted in the next email. Alerting never raises: a failed send is logged.
Alert emails carry `X-Gmail-Integration: alert` (the sync skips them) and
the `Sistema/Alerta` label.
"""
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText
from typing import Literal, Protocol

from googleapiclient.discovery import Resource
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.ports import Labeler
from src.db.accounts import admin_account_id
from src.db.models import Notification
from src.db.scope import account_scope
from src.db.store import Store
from src.gmail.clients import GmailClients
from src.gmail.messages import SYSTEM_HEADER
from src.jobs.types import ClaimedJob
from src.jobs.worker import Handler
from src.utils.console import log

AlertKind = Literal[
    "job_dead", "agent_failures", "sync_failures", "watch_failures", "backfill_done",
    "ip_blocked", "global_lock", "refresh_reuse", "login_new_ip", "gmail_revoked",
]
AlertOutcome = Literal["sent", "suppressed", "failed"]

ALERT_HEADER = SYSTEM_HEADER
SUBJECT_PREFIX = "[gmail-integration] "
ALERT_LABEL = "Sistema/Alerta"
WINDOW = timedelta(minutes=15)
USER_ID = "me"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MailSender(Protocol):
    def send_to_self(self, subject: str, body: str) -> None: ...


class _Sent(BaseModel):
    id: str


class GmailMailSender:
    def __init__(
        self,
        service_factory: Callable[[], Resource],
        labels: Labeler,
        address: Callable[[], str | None],
    ) -> None:
        self._service = service_factory
        self._labels = labels
        self._address = address

    def send_to_self(self, subject: str, body: str) -> None:
        to = self._address()
        if not to:
            raise RuntimeError("endereço do usuário desconhecido (watch_state vazio)")
        mime = MIMEText(body, "plain", "utf-8")
        mime["To"] = to
        mime["From"] = to
        mime["Subject"] = SUBJECT_PREFIX + subject
        mime[ALERT_HEADER] = "alert"
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = _Sent.model_validate(
            self._service().users().messages().send(userId=USER_ID, body={"raw": raw}).execute(
                num_retries=3
            )
        )
        self._labels.apply(sent.id, [ALERT_LABEL])


class AlertService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        sender: MailSender,
        clock: Callable[[], datetime] = _utcnow,
        account: Callable[[], int | None] | None = None,
    ) -> None:
        """Alerts are operational: they go to (and are logged in) the admin's
        account, whatever account the caller is running for."""
        self.sf = session_factory
        self.sender = sender
        self._now = clock
        self._account = account or (lambda: admin_account_id(session_factory))

    def _window(self, kind: AlertKind, now: datetime) -> tuple[bool, int]:
        """(inside the suppression window?, suppressed since the last send)."""
        with self.sf() as s:
            last_sent = s.scalar(
                select(func.max(Notification.created_at)).where(
                    Notification.kind == kind, Notification.status == "sent"
                )
            )
            if last_sent is not None and last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=UTC)  # sqlite drops tzinfo
            if last_sent is not None and now - last_sent < WINDOW:
                return True, 0
            since = last_sent or datetime.min.replace(tzinfo=UTC)
            suppressed = s.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.kind == kind,
                    Notification.status == "suppressed",
                    Notification.created_at > since,
                )
            )
        return False, suppressed or 0

    def alert(self, kind: AlertKind, subject: str, body: str) -> AlertOutcome:
        account = self._account()
        if account is None:
            log(f"alerta {kind} sem conta de admin: {subject}")
            return "failed"
        with account_scope(account):
            return self._alert(kind, subject, body)

    def _alert(self, kind: AlertKind, subject: str, body: str) -> AlertOutcome:
        now = self._now()
        inside, suppressed = self._window(kind, now)
        outcome: AlertOutcome
        error: str | None = None
        if inside:
            outcome = "suppressed"
        else:
            if suppressed:
                body += (
                    f"\n\n+{suppressed} alerta(s) suprimido(s) deste tipo desde o último envio."
                )
            try:
                self.sender.send_to_self(subject, body)
                outcome = "sent"
            except Exception as exc:  # noqa: BLE001 - alerting must never break callers
                outcome, error = "failed", f"{type(exc).__name__}: {exc}"
                log(f"alerta {kind} não enviado: {error}")
        with self.sf.begin() as s:
            s.add(
                Notification(
                    kind=kind,
                    subject=subject,
                    body=body,
                    status=outcome,
                    error=error,
                    created_at=now,
                    sent_at=now if outcome == "sent" else None,
                )
            )
        return outcome


def build_alert_service(store: Store, clients: GmailClients | None = None) -> AlertService:
    """Real alert service: Gmail send (as the admin account) to its own
    address, labeled."""
    from src.gmail.labels import GmailLabeler

    gmail = (clients or GmailClients(store.sf)).getter()

    def address() -> str | None:
        state = store.get_watch_state()
        return state.email_address if state else None

    sender = GmailMailSender(gmail, GmailLabeler(gmail, store.sf), address)
    return AlertService(store.sf, sender)


def gmail_revoked_hook(alerts: AlertService) -> Callable[[int, str], None]:
    """`GmailClients.on_revoked`: tells the admin, naming only the account and
    the error kind (never mail content)."""

    def hook(account_id: int, email: str) -> None:
        alerts.alert(
            "gmail_revoked",
            f"Gmail de {email} desconectado",
            f"O Google recusou o acesso da conta {email} (conta {account_id}, erro invalid_grant). "
            "A pessoa precisa reconectar o Gmail no painel.",
        )

    return hook


class FailureStreak:
    """Calls `on_trip(count, last_error)` from the `threshold`-th consecutive
    failure on (aggregation in AlertService keeps the mail volume sane)."""

    def __init__(self, threshold: int, on_trip: Callable[[int, str], object]) -> None:
        self.threshold = threshold
        self.on_trip = on_trip
        self.count = 0

    def success(self) -> None:
        self.count = 0

    def failure(self, error: str) -> None:
        self.count += 1
        if self.count >= self.threshold:
            try:
                self.on_trip(self.count, error)
            except Exception as exc:  # noqa: BLE001
                log(f"alerta de falhas em série não enviado: {exc}")

    def guard(self, handler: Handler) -> Handler:
        def guarded(job: ClaimedJob) -> None:
            try:
                handler(job)
            except Exception as exc:
                self.failure(f"{type(exc).__name__}: {exc}")
                raise
            self.success()

        return guarded
