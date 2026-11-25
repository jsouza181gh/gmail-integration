"""CLI: moves the `.env` Gemini keys into a user's own keys (BYOK).

Uso (na VPS, depois do deploy):
    docker compose exec api python -m src.cli.import_env_keys --account <e-mail>

GOOGLE_API_KEY vira a chave do app e GOOGLE_API_KEY_BACKFILL (se houver e for
outra) a do backfill, guardadas cifradas na linha do usuário. Até este comando
rodar, só a conta admin usa as chaves do .env (com aviso no log); depois, o
.env pode perder as duas variáveis. As chaves nunca são impressas.
"""
import argparse
import sys
from collections.abc import Callable

from dotenv import load_dotenv

load_dotenv()  # before importing modules that read env at import time

from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from src.db.accounts import account_by_email  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.llm.config import env_account_keys  # noqa: E402
from src.llm.keys import AccountKeys, save_keys, user_id_for_account, validate_key  # noqa: E402


def import_env_keys(
    sf: sessionmaker[Session],
    email: str,
    env: AccountKeys | None,
    *,
    validator: Callable[[str], str | None] | None = validate_key,
) -> str:
    """The summary line to print; raises SystemExit with a reason when it cannot import."""
    if env is None:
        raise SystemExit("GOOGLE_API_KEY não está definida no ambiente: nada a importar")
    account = account_by_email(sf, email)
    user_id = None if account is None else user_id_for_account(sf, account)
    if user_id is None:
        raise SystemExit(f"conta não encontrada: {email.strip().lower()}")
    if validator is not None:
        for label, key in (("app", env.app), ("backfill", env.backfill)):
            problem = None if key is None else validator(key)
            if problem is not None:
                raise SystemExit(f"a chave {label} do .env não passou na validação: {problem}")
    save_keys(sf, user_id, app_key=env.app, backfill_key=env.backfill)
    second = "com chave de backfill separada" if env.separate else "uma chave só (app e backfill)"
    return f"chaves do .env importadas para {email.strip().lower()}: {second}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--account", required=True, help="e-mail da conta que recebe as chaves")
    parser.add_argument(
        "--no-validate", action="store_true", help="não testa as chaves no Google antes de guardar"
    )
    args = parser.parse_args(argv)
    store = Store()
    try:
        print(import_env_keys(
            store.sf, args.account, env_account_keys(),
            validator=None if args.no_validate else validate_key,
        ))
    finally:
        store.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
