from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, GmailAccount
from src.db.scope import account_scope, system_scope
from src.db.session import make_session_factory


def add_account(sf: sessionmaker[Session], account_id: int, email: str) -> int:
    with system_scope(), sf.begin() as s:
        s.add(GmailAccount(id=account_id, user_id=account_id, email_address=email,
                           status="connected", connected_at=datetime.now(UTC)))
    return account_id


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    sf = make_session_factory("sqlite:///:memory:")
    Base.metadata.create_all(sf.kw["bind"])
    add_account(sf, 1, "me@example.com")
    return sf


@pytest.fixture
def second_account(session_factory: sessionmaker[Session]) -> int:
    return add_account(session_factory, 2, "other@example.com")


@pytest.fixture(autouse=True)
def _account_scope(request: pytest.FixtureRequest) -> Iterator[None]:
    """Tests run as account 1 unless marked `noscope` (entry-point tests)."""
    if request.node.get_closest_marker("noscope"):
        yield
        return
    with account_scope(1):
        yield
