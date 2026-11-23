"""CLI commands run in one account: the only one, or the one named."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.cli.account import resolve_cli_account

pytestmark = pytest.mark.noscope


def test_single_account_is_default(session_factory: sessionmaker[Session]) -> None:
    assert resolve_cli_account(session_factory, None) == 1


def test_requires_account_when_many(
    session_factory: sessionmaker[Session], second_account: int
) -> None:
    with pytest.raises(SystemExit, match=r"informe --account <e-mail> \(há 2 contas\)"):
        resolve_cli_account(session_factory, None)
    assert resolve_cli_account(session_factory, "Other@Example.com") == second_account


def test_unknown_account_errors(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(SystemExit, match="conta não encontrada: x@y.z"):
        resolve_cli_account(session_factory, "x@y.z")
