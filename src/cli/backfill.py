"""CLI: backfill do histórico (índice + classificação + anexos).

Uso:
  python -m src.cli.backfill start    # cria/retoma; seguro repetir (repara a corrente)
  python -m src.cli.backfill status

O trabalho roda no worker, uma página de 25 mensagens por job, com a menor
prioridade da fila. Sobrevive a deploys: o cursor fica no banco.
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.cli.account import add_account_arg, cli_account  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.indexing.backfill import BackfillProgress  # noqa: E402
from src.indexing.wiring import Indexing  # noqa: E402
from src.jobs.store import JobStore  # noqa: E402
from src.llm.config import build_backfill_gateway  # noqa: E402


def render(p: BackfillProgress) -> str:
    state = "concluído" if p.fetch_done else "em andamento"
    lines = [
        f"backfill (run {p.run_id}): {state}, {p.pages} página(s) lidas",
        f"e-mails: {p.emails} | classificados: {p.classified} | indexados: {p.indexed}"
        f" | com vetor: {p.embedded}",
        "anexos: " + (", ".join(f"{k}={v}" for k, v in sorted(p.attachments.items())) or "nenhum"),
        "fila:",
    ]
    for kind, c in p.jobs.items():
        lines.append(f"  {kind:<18} queued={c.queued} running={c.running} done={c.done} dead={c.dead}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("cmd", choices=["start", "status"])
    add_account_arg(parser)
    args = parser.parse_args(argv)
    store = Store()
    try:
        with cli_account(store.sf, args.account):
            jobs = JobStore(store.sf)
            backfill = Indexing(store, jobs, build_backfill_gateway()).backfill
            progress = backfill.start() if args.cmd == "start" else backfill.progress()
            print(render(progress))
    finally:
        store.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
