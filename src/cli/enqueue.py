"""CLI: enfileira `process_email` para e-mails que o agente ainda não processou.

Uso: python -m src.cli.enqueue [--days N]

Sem --days, considera todos os e-mails do banco. Idempotente: e-mails que já
têm job (de qualquer estado) não são enfileirados de novo.
"""
import argparse
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from src.cli.account import add_account_arg, cli_account  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.jobs.store import JobStore  # noqa: E402
from src.jobs.types import ProcessEmailJob  # noqa: E402
from src.utils.console import log  # noqa: E402


def run(store: Store, jobs: JobStore, days: int | None) -> int:
    since = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    ids = store.emails_without_agent_run(since)
    enqueued = sum(jobs.enqueue(ProcessEmailJob(email_id=i)) for i in ids)
    log(f"{len(ids)} e-mail(s) sem processamento; {enqueued} job(s) novo(s)")
    return enqueued


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None, help="só e-mails recebidos nos últimos N dias")
    add_account_arg(parser)
    args = parser.parse_args(argv)
    store = Store()
    try:
        with cli_account(store.sf, args.account):
            run(store, JobStore(store.sf), args.days)
    finally:
        store.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
