from datetime import UTC, datetime, timedelta
from ipaddress import ip_network

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import (
    issue_access,
    issue_download,
    read_access,
    read_download,
)
from src.auth.security_log import SecurityLog
from src.auth.service import AuthService
from src.db.models import SecurityEvent, User
from tests.auth_fakes import make_user, secret_of

SETTINGS = AuthSettings(
    jwt_secret="j" * 40,
    app_secret="2xJ0yNw0b7a1Y8l8Cq0Rk3F2nQm8sV9pQ0rW4tZ6uX8=",
    origin="https://x.org",
    production=True,
    trusted_proxies=(ip_network("127.0.0.1/32"),),
)
PASSWORD = "senha-forte-123"


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


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def alerts() -> Alerts:
    return Alerts()


@pytest.fixture
def world(session_factory: sessionmaker[Session], clock: Clock, alerts: Alerts) -> tuple[AuthService, str, list[str]]:
    enrollment = make_user(session_factory, SETTINGS, "joao", PASSWORD, email="joao@example.com",
                           role="admin", clock=clock)
    secret = secret_of(enrollment)
    svc = AuthService(session_factory, SETTINGS, SecurityLog(session_factory, alerts, clock), clock)
    return svc, secret, list(enrollment.recovery_codes)


def totp(secret: str, clock: Clock) -> str:
    return pyotp.TOTP(secret).at(clock())


def events(sf: sessionmaker[Session]) -> list[str]:
    with sf() as s:
        return list(s.scalars(select(SecurityEvent.kind).order_by(SecurityEvent.id)))


def login(svc: AuthService, secret: str, clock: Clock, ip: str = "1.1.1.1"):  # noqa: ANN201
    step = svc.password_step("joao", PASSWORD, ip)
    assert step.ok and step.user_id is not None
    issued = svc.second_step(step.user_id, totp(secret, clock), ip, "ua")
    assert issued is not None
    return issued


def test_password_then_totp_issues_session(world, clock, session_factory) -> None:  # noqa: ANN001
    svc, secret, _ = world
    issued = login(svc, secret, clock)
    assert read_access(issued.access, SETTINGS, clock()) == 1
    assert len(issued.refresh) > 30 and len(issued.csrf) > 30
    assert events(session_factory) == ["login_ok", "login_new_ip"]


def test_wrong_totp_counts_as_failure(world, clock, session_factory) -> None:  # noqa: ANN001
    svc, _, _ = world
    step = svc.password_step("joao", PASSWORD, "1.1.1.1")
    assert step.user_id is not None
    assert svc.second_step(step.user_id, "000000", "1.1.1.1", "ua") is None
    assert events(session_factory) == ["totp_fail"]


def test_recovery_code_single_use(world, clock) -> None:  # noqa: ANN001
    svc, _, codes = world
    assert len(codes) == 10
    assert svc.second_step(1, codes[0], "1.1.1.1", "ua") is not None
    assert svc.second_step(1, codes[0], "1.1.1.1", "ua") is None
    assert svc.second_step(1, codes[1].upper(), "1.1.1.1", "ua") is not None  # case-insensitive


def test_third_failure_blocks_ip_even_with_right_password_next(world, clock, alerts, session_factory) -> None:  # noqa: ANN001
    svc, _, _ = world
    for _ in range(3):
        assert not svc.password_step("joao", "errada", "6.6.6.6").ok
    step = svc.password_step("joao", PASSWORD, "6.6.6.6")
    assert (step.ok, step.blocked) == (False, True)
    assert svc.check_open("6.6.6.6") == "ip_blocked" and svc.check_open("1.1.1.1") == "ok"
    assert alerts.sent == ["ip_blocked"]
    assert events(session_factory).count("ip_blocked") == 1


def test_unknown_user_counts_too(world) -> None:  # noqa: ANN001
    svc, _, _ = world
    for _ in range(3):
        svc.password_step("ninguem", "x", "6.6.6.7")
    assert svc.check_open("6.6.6.7") == "ip_blocked"


def test_failures_from_three_ips_lock_that_user_only(world, clock, alerts, session_factory) -> None:  # noqa: ANN001
    svc, secret, _ = world
    other = make_user(session_factory, SETTINGS, "ana", PASSWORD, email="ana@example.com", clock=clock)
    for ip in ("2.2.2.2", "3.3.3.3", "4.4.4.4"):
        svc.password_step("joao", "errada", ip)
    assert svc.user_locked(1) and not svc.user_locked(2)
    assert svc.check_open("1.1.1.1") == "ok"  # no IP is blocked: the site stays open
    step = svc.password_step("joao", PASSWORD, "1.1.1.1")
    assert (step.ok, step.blocked) == (False, True)
    assert svc.password_step("ana", PASSWORD, "1.1.1.1").ok  # other users keep logging in
    assert svc.second_step(1, totp(secret, clock), "1.1.1.1", "ua") is None  # even past the password
    assert svc.second_step(2, totp(secret_of(other), clock), "1.1.1.1", "ua") is not None
    assert "global_lock" in alerts.sent and "user_lock" in events(session_factory)
    assert svc.locked_users()[0][:2] == (1, "joao@example.com")


def test_failures_against_different_users_do_not_add_up(world, clock, session_factory) -> None:  # noqa: ANN001
    svc, _, _ = world
    make_user(session_factory, SETTINGS, "ana", PASSWORD, email="ana@example.com", clock=clock)
    make_user(session_factory, SETTINGS, "bia", PASSWORD, email="bia@example.com", clock=clock)
    for ip, who in (("2.2.2.2", "joao"), ("3.3.3.3", "ana"), ("4.4.4.4", "bia")):
        svc.password_step(who, "errada", ip)
    assert not any(svc.user_locked(i) for i in (1, 2, 3))


def test_google_step_respects_the_user_lock(world, clock) -> None:  # noqa: ANN001
    svc, _, _ = world
    assert svc.google_step(1).ok
    for ip in ("2.2.2.2", "3.3.3.3", "4.4.4.4"):
        svc.password_step("joao", "errada", ip)
    assert svc.google_step(1).blocked


def test_login_by_email_and_no_password_or_totp_means_no_password_login(world, clock, session_factory) -> None:  # noqa: ANN001
    svc, _, _ = world
    assert svc.password_step("Joao@Example.com", PASSWORD, "1.1.1.1").ok
    make_user(session_factory, SETTINGS, "nopass", None, email="nopass@example.com", clock=clock)
    make_user(session_factory, SETTINGS, "nototp", PASSWORD, email="nototp@example.com", totp=False,
              clock=clock)
    assert not svc.password_step("nopass", "qualquer-coisa-12345", "1.1.1.1").ok
    assert not svc.password_step("nototp", PASSWORD, "1.1.1.1").ok  # a password alone enrolls nobody
    assert svc.second_step(4, "123456", "1.1.1.1", "ua") is None  # no secret to check


def test_old_failures_expire(world, clock) -> None:  # noqa: ANN001
    svc, _, _ = world
    svc.password_step("joao", "errada", "2.2.2.2")
    svc.password_step("joao", "errada", "3.3.3.3")
    clock.advance(hours=25)
    svc.password_step("joao", "errada", "4.4.4.4")
    assert svc.check_open("1.1.1.1") == "ok"


def test_unlock_ip_and_user(world, clock, session_factory) -> None:  # noqa: ANN001
    svc, secret, _ = world
    for _ in range(3):
        svc.password_step("joao", "errada", "6.6.6.6")
    assert svc.unlock_ip("6.6.6.6") and not svc.unlock_ip("6.6.6.6")
    assert svc.check_open("6.6.6.6") == "ok"
    svc.password_step("joao", "errada", "6.6.6.6")  # old failures do not re-block at once
    assert svc.check_open("6.6.6.6") == "ok"
    for ip in ("2.2.2.2", "3.3.3.3"):
        svc.password_step("joao", "errada", ip)
    assert svc.user_locked(1)
    assert svc.unlock_user(1) and not svc.unlock_user(1) and not svc.user_locked(1)
    login(svc, secret, clock)
    assert "unlock" in events(session_factory)


def test_refresh_rotates_and_reuse_revokes_family(world, clock, alerts, session_factory) -> None:  # noqa: ANN001
    svc, secret, _ = world
    first = login(svc, secret, clock)
    clock.advance(minutes=11)
    second = svc.refresh(first.refresh, "1.1.1.1", "ua")
    assert second is not None and second.refresh != first.refresh
    assert second.family_id == first.family_id
    clock.advance(minutes=1)
    assert svc.refresh(first.refresh, "9.9.9.9", "evil") is None  # stolen copy replayed
    assert svc.refresh(second.refresh, "1.1.1.1", "ua") is None  # family is dead
    assert "refresh_reuse" in events(session_factory) and alerts.sent.count("refresh_reuse") == 1


def test_concurrent_refresh_within_grace_does_not_revoke(world, clock) -> None:  # noqa: ANN001
    svc, secret, _ = world
    first = login(svc, secret, clock)
    second = svc.refresh(first.refresh, "1.1.1.1", "ua")
    assert second is not None
    clock.advance(seconds=3)
    assert svc.refresh(first.refresh, "1.1.1.1", "ua") is None  # the other tab lost the race
    assert svc.refresh(second.refresh, "1.1.1.1", "ua") is not None  # family still alive


def test_expired_refresh_rejected(world, clock) -> None:  # noqa: ANN001
    svc, secret, _ = world
    issued = login(svc, secret, clock)
    clock.advance(days=15)
    assert svc.refresh(issued.refresh, "1.1.1.1", "ua") is None


def test_logout_revokes(world, clock) -> None:  # noqa: ANN001
    svc, secret, _ = world
    issued = login(svc, secret, clock)
    svc.logout(issued.refresh)
    assert svc.refresh(issued.refresh, "1.1.1.1", "ua") is None


def test_revoke_family_from_sessions_list(world, clock) -> None:  # noqa: ANN001
    svc, secret, _ = world
    a = login(svc, secret, clock, ip="1.1.1.1")
    clock.advance(seconds=31)
    b = login(svc, secret, clock, ip="5.5.5.5")
    sessions = svc.sessions(1)
    assert {s.ip for s in sessions} == {"1.1.1.1", "5.5.5.5"}
    assert svc.revoke_family(1, a.family_id) and not svc.revoke_family(1, "nope")
    assert svc.refresh(a.refresh, "1.1.1.1", "ua") is None
    assert svc.refresh(b.refresh, "5.5.5.5", "ua") is not None


def test_new_ip_login_alerts(world, clock, alerts) -> None:  # noqa: ANN001
    svc, secret, _ = world
    login(svc, secret, clock, ip="1.1.1.1")
    clock.advance(seconds=31)
    login(svc, secret, clock, ip="1.1.1.1")
    clock.advance(seconds=31)
    login(svc, secret, clock, ip="5.5.5.5")
    assert alerts.sent.count("login_new_ip") == 2


def test_totp_secret_encrypted_at_rest(world, session_factory) -> None:  # noqa: ANN001
    _, secret, _ = world
    with session_factory() as s:
        user = s.get_one(User, 1)
    assert secret not in user.totp_secret_enc and user.password_hash.startswith("$argon2id$")


def test_access_token_expires(clock: Clock) -> None:
    token = issue_access(7, SETTINGS, clock())
    assert read_access(token, SETTINGS, clock() + timedelta(seconds=599)) == 7
    assert read_access(token, SETTINGS, clock() + timedelta(seconds=601)) is None
    assert read_access(token + "x", SETTINGS, clock()) is None


def test_download_token_bound_to_attachment(clock: Clock) -> None:
    token = issue_download(5, SETTINGS, clock())
    assert read_download(token, 5, SETTINGS, clock())
    assert not read_download(token, 6, SETTINGS, clock())
    assert not read_download(token, 5, SETTINGS, clock() + timedelta(seconds=61))
    assert read_access(token, SETTINGS, clock()) is None  # not an access token
