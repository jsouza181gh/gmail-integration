import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.cli.import_env_keys import import_env_keys
from src.db.models import User
from src.db.scope import system_scope
from src.llm.config import env_account_keys
from src.llm.keys import AccountKeys, keys_for_account
from tests.test_llm_keys import FERNET, NOW

pytestmark = pytest.mark.noscope


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET", FERNET)


@pytest.fixture
def sf(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with system_scope(), session_factory.begin() as s:
        s.add(User(id=1, username="joao", email="me@example.com", password_hash="x",
                   totp_secret_enc="x", created_at=NOW, password_changed_at=NOW, role="admin"))
    return session_factory


def test_moves_both_env_keys_to_the_users_row(sf: sessionmaker[Session]) -> None:
    out = import_env_keys(sf, "Me@Example.com", AccountKeys(app="A", backfill="B"),
                          validator=lambda k: None)
    assert "separada" in out
    assert keys_for_account(sf, 1) == AccountKeys(app="A", backfill="B")


def test_single_env_key(sf: sessionmaker[Session]) -> None:
    import_env_keys(sf, "me@example.com", AccountKeys(app="A"), validator=None)
    assert keys_for_account(sf, 1) == AccountKeys(app="A", backfill=None)


def test_refuses_a_key_that_does_not_validate(sf: sessionmaker[Session]) -> None:
    with pytest.raises(SystemExit, match="não passou na validação"):
        import_env_keys(sf, "me@example.com", AccountKeys(app="A"), validator=lambda k: "ruim")
    assert keys_for_account(sf, 1) is None


def test_unknown_account_and_missing_env(sf: sessionmaker[Session]) -> None:
    with pytest.raises(SystemExit, match="conta não encontrada"):
        import_env_keys(sf, "x@y.z", AccountKeys(app="A"), validator=None)
    with pytest.raises(SystemExit, match="nada a importar"):
        import_env_keys(sf, "me@example.com", None, validator=None)


def test_env_keys_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert env_account_keys() is None
    monkeypatch.setenv("GOOGLE_API_KEY", "A")
    monkeypatch.setenv("GOOGLE_API_KEY_BACKFILL", "B")
    assert env_account_keys() == AccountKeys(app="A", backfill="B")
    monkeypatch.setenv("GOOGLE_API_KEY_BACKFILL", "A")
    assert env_account_keys() == AccountKeys(app="A", backfill=None)
