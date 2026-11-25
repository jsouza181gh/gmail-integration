"""Build the API with real auth over an in-memory DB and log in through HTTP."""
from datetime import UTC, datetime, timedelta
from ipaddress import ip_network

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.api.deps import ApiDeps
from src.auth.config import AuthSettings
from tests.auth_fakes import make_user, secret_of

ORIGIN = "https://panel.test"
PASSWORD = "senha-forte-123"
SETTINGS = AuthSettings(
    jwt_secret="j" * 40,
    app_secret="2xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8=",
    origin=ORIGIN,
    production=True,
    trusted_proxies=(ip_network("127.0.0.1/32"),),
)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)


class Alerts:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def alert(self, kind: str, subject: str, body: str) -> str:
        self.sent.append(kind)
        return "sent"


class Api:
    def __init__(self, sf: sessionmaker[Session], **deps_kw: object) -> None:
        self.sf = sf
        self.clock = Clock()
        self.alerts = Alerts()
        enrollment = make_user(sf, SETTINGS, "joao", PASSWORD, role="admin",
                               email="joao@example.com", clock=self.clock)
        self.secret = secret_of(enrollment)
        self.recovery = enrollment.recovery_codes
        self.deps = ApiDeps.build(sf, SETTINGS, alerts=self.alerts, clock=self.clock, **deps_kw)  # type: ignore[arg-type]
        self.app = create_app(trigger=lambda: None, token=None, api=self.deps)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        return TestClient(self.app, base_url="https://testserver", headers={"Origin": ORIGIN})

    def code(self) -> str:
        return pyotp.TOTP(self.secret).at(self.clock())

    def login(self) -> None:
        r = self.client.post("/api/auth/login", json={"username": "joao", "password": PASSWORD})
        assert r.status_code == 204, r.text
        r = self.client.post("/api/auth/2fa", json={"code": self.code()})
        assert r.status_code == 204, r.text
        self.client.headers["X-CSRF-Token"] = self.client.cookies["csrf"]
