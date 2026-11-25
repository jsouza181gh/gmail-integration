"""CLI: documentos indexados — busca semântica, lista e download sob demanda.

Uso:
  python -m src.cli.docs search "boleto de energia" [--k 10]
  python -m src.cli.docs list [--class fiscal|pessoal|trabalho] [--type boleto] [--limit 50]
  python -m src.cli.docs get <attachment_id> --out <arquivo>

O anexo nunca é guardado no servidor: `get` baixa do Gmail na hora.
"""
import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import Resource

load_dotenv()

from src.cli.account import add_account_arg, cli_account  # noqa: E402
from src.gmail.full import fetch_attachment  # noqa: E402
from src.indexing.store import IndexStore  # noqa: E402
from src.utils.console import log  # noqa: E402


def _money(cents: int | None) -> str:
    if cents is None:
        return ""
    reais = f"{cents // 100:,}".replace(",", ".")
    return f" R$ {reais},{cents % 100:02d}"


def list_documents(store: IndexStore, doc_class: str | None, doc_type: str | None, limit: int) -> str:
    rows = store.documents(doc_class, doc_type, limit)
    if not rows:
        return "nenhum documento"
    return "\n".join(
        f"#{r.id} [{r.doc_class}/{r.doc_type}] {r.title or r.filename}"
        f"{_money(r.amount_cents)}{f' vence {r.due_on}' if r.due_on else ''}"
        for r in rows
    )


def download(
    store: IndexStore, service_factory: Callable[[], Resource], attachment_id: int, out: Path
) -> str:
    row = store.attachment(attachment_id)
    if row is None:
        raise LookupError(f"anexo {attachment_id} não existe")
    data = fetch_attachment(service_factory(), row.email_id, row.part_id)
    out.write_bytes(data)
    log(f"download do anexo #{attachment_id} ({len(data)} bytes)")
    return f"salvo em {out} ({len(data)} bytes)"


def search(store: IndexStore, query: str, k: int) -> str:
    from src.llm.accounts import AccountLLMs
    from src.llm.config import env_account_keys
    from src.llm.types import EmbedInput

    llms = AccountLLMs(store.sf, inline_wait_s=None, env_keys=env_account_keys)
    vec = llms.app.embed([EmbedInput(text=query)], purpose="search", task="RETRIEVAL_QUERY")
    hits = store.search(vec.vectors[0], k)
    return "\n".join(f"{h.score:.3f} {h.kind:<10} {h.id:<20} {h.title[:100]}" for h in hits) or "nada"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=10)
    p_list = sub.add_parser("list")
    p_list.add_argument("--class", dest="doc_class")
    p_list.add_argument("--type", dest="doc_type")
    p_list.add_argument("--limit", type=int, default=50)
    p_get = sub.add_parser("get")
    p_get.add_argument("attachment_id", type=int)
    p_get.add_argument("--out", type=Path, required=True)
    for sp in (p_search, p_list, p_get):
        add_account_arg(sp)
    args = parser.parse_args(argv)

    from src.db.store import Store

    db = Store()
    try:
        with cli_account(db.sf, args.account):
            store = IndexStore(db.sf)
            if args.cmd == "search":
                print(search(store, args.query, args.k))
            elif args.cmd == "list":
                print(list_documents(store, args.doc_class, args.doc_type, args.limit))
            else:
                from src.gmail.clients import GmailClients

                print(download(store, GmailClients(db.sf).getter(), args.attachment_id, args.out))
    finally:
        db.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
