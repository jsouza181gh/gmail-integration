import pytest

from src.db.models import Base
from src.db.session import make_session_factory


@pytest.fixture
def session_factory():
    sf = make_session_factory("sqlite:///:memory:")
    Base.metadata.create_all(sf.kw["bind"])
    return sf
