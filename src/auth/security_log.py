"""security_events: what happened to the account and the HTTP surface.

Some kinds also mail an alert (aggregated per kind by the alert service).
"""
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy.orm import Session, sessionmaker

from src.alerts.service import AlertKind
from src.db.models import SecurityEvent
from src.utils.console import log

SecurityKind = Literal[
    "login_ok", "login_fail", "totp_fail", "ip_blocked", "global_lock", "refresh_reuse",
    "login_new_ip", "logout", "session_revoked", "csrf_invalid", "download", "unlock",
    "user_lock", "invite_created", "signup", "onboarding_done",
]
DETAIL_MAX = 1000

_ALERTED: dict[SecurityKind, AlertKind] = {
    "ip_blocked": "ip_blocked",
    "global_lock": "global_lock",
    "user_lock": "global_lock",
    "refresh_reuse": "refresh_reuse",
    "login_new_ip": "login_new_ip",
}


class Alerter(Protocol):
    def alert(self, kind: AlertKind, subject: str, body: str) -> object: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SecurityLog:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        alerts: Alerter,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sf = session_factory
        self.alerts = alerts
        self._now = clock

    def record(self, kind: SecurityKind, ip: str | None, detail: str) -> None:
        with self.sf.begin() as s:
            s.add(SecurityEvent(kind=kind, ip=ip, detail=detail[:DETAIL_MAX], created_at=self._now()))
        log(f"segurança: {kind} ({ip or '-'})")
        alert = _ALERTED.get(kind)
        if alert is not None:
            self.alerts.alert(alert, f"[gmail-integration] segurança: {kind}", f"IP: {ip}\n{detail}")
