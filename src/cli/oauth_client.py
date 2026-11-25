"""CLI: the platform's Google OAuth client.

Uso:
  docker compose exec api python -m src.cli.oauth_client sync

Copia GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET do ambiente para a
linha `platform` de oauth_clients (o secret fica cifrado). Rode de novo ao
trocar o secret.
"""
import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy.orm import Session, sessionmaker

from src.gmail.oauth_clients import upsert_client


def sync_platform_client(sf: sessionmaker[Session]) -> int:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("defina GOOGLE_OAUTH_CLIENT_ID e GOOGLE_OAUTH_CLIENT_SECRET")
    return upsert_client(sf, "platform", client_id, client_secret)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    from src.db.store import Store

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["sync"])
    parser.parse_args(argv)
    store = Store()
    try:
        client = sync_platform_client(store.sf)
    finally:
        store.close()
    print(f"cliente OAuth da plataforma atualizado (id {client})")
    sys.exit(0)


if __name__ == "__main__":
    main()
