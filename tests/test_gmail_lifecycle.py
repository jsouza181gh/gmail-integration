import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.gmail.clients import GmailClients
from src.gmail.lifecycle import REVOKE_URL, revoke_token, stop_watch
from tests.oauth_fakes import FakeRefresher, app_secret, connect  # noqa: F401


class FakeUsers:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def stop(self, userId: str) -> "FakeUsers":  # noqa: N803 - Gmail API spelling
        self.stopped.append(userId)
        return self

    def execute(self, num_retries: int = 0) -> dict[str, str]:
        return {}


class FakeService:
    def __init__(self) -> None:
        self.fake_users = FakeUsers()

    def users(self) -> FakeUsers:
        return self.fake_users


def test_stop_watch_calls_users_stop_for_that_account(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    connect(session_factory, 1, "me@example.com")
    connect(session_factory, 2, "other@example.com")
    services: dict[str | None, FakeService] = {}

    def build(creds: object) -> FakeService:
        svc = FakeService()
        services[getattr(creds, "refresh_token", None)] = svc
        return svc

    clients = GmailClients(session_factory, refresh=FakeRefresher(), build_service=build)
    stop_watch(clients, 2)
    assert [s.fake_users.stopped for s in services.values()] == [["me"]]


def test_revoke_token_posts_the_plain_refresh_token(session_factory: sessionmaker[Session]) -> None:
    connect(session_factory, 1, "me@example.com", refresh_token="rt-plain")
    calls: list[tuple[str, dict[str, str]]] = []

    def http(url: str, data: dict[str, str]) -> tuple[int, str]:
        calls.append((url, data))
        return 200, "{}"

    revoke_token(session_factory, 1, http)
    assert calls == [(REVOKE_URL, {"token": "rt-plain"})]


def test_revoke_token_tolerates_already_invalid_and_reports_other_failures(
    session_factory: sessionmaker[Session],
) -> None:
    connect(session_factory, 1, "me@example.com")
    revoke_token(session_factory, 1, lambda url, data: (400, '{"error":"invalid_token"}'))
    with pytest.raises(RuntimeError):
        revoke_token(session_factory, 1, lambda url, data: (500, "oops"))


def test_revoke_token_without_token_is_a_no_op(session_factory: sessionmaker[Session]) -> None:
    revoke_token(session_factory, 1, lambda url, data: (500, "never called"))
