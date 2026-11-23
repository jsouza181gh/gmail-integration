"""`--account <e-mail>`: which Gmail account a content command runs for.

Without it the command runs for the only connected account and refuses to
guess when there are several.
"""
import argparse
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session, sessionmaker

from src.db.accounts import account_by_email, connected_account_ids
from src.db.scope import account_scope


def add_account_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--account", default=None, help="e-mail da conta (obrigatório com mais de uma conta)"
    )


def resolve_cli_account(sf: sessionmaker[Session], email: str | None) -> int:
    if email is not None:
        account = account_by_email(sf, email)
        if account is None:
            raise SystemExit(f"conta não encontrada: {email.strip().lower()}")
        return account
    ids = connected_account_ids(sf)
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise SystemExit("nenhuma conta conectada")
    raise SystemExit(f"informe --account <e-mail> (há {len(ids)} contas)")


def cli_account(sf: sessionmaker[Session], email: str | None) -> AbstractContextManager[None]:
    return account_scope(resolve_cli_account(sf, email))
