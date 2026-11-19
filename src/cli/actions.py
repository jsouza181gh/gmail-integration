"""CLI: fila de aprovação do agente.

Uso:
  python -m src.cli.actions list [--sender S] [--kind K] [--rule N]
  python -m src.cli.actions approve (--id N... | --sender S | --rule N | --kind K) [--comment T] [--yes]
  python -m src.cli.actions reject  (--id N... | --sender S | --rule N | --kind K) [--comment T] [--yes]

Só ações pendentes são afetadas. Toda decisão vira feedback; um comentário
também vira entrada da KB que o agente lê nas próximas decisões.
"""
import argparse
from collections.abc import Callable, Sequence

from dotenv import load_dotenv

from src.agent.approvals import ApprovalService
from src.db.rows import PendingActionRow

Out = Callable[[str], None]
Ask = Callable[[str], str]


def _fmt(r: PendingActionRow) -> str:
    sender = (r.sender or "")[:30]
    subject = (r.subject or "")[:40]
    return f"#{r.id:<5} {r.kind:<12} {sender:<30} {subject:<40} {r.origin:<5} {r.reason[:60]}"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.cli.actions", description="fila de aprovação")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--sender")
    ls.add_argument("--kind")
    ls.add_argument("--rule", type=int)
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("--id", type=int, nargs="+", default=[])
        d.add_argument("--sender")
        d.add_argument("--kind")
        d.add_argument("--rule", type=int)
        d.add_argument("--comment")
        d.add_argument("--yes", action="store_true")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    service: ApprovalService | None = None,
    out: Out = print,
    ask: Ask = input,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if service is None:
        load_dotenv()
        from src.db.store import Store
        from src.jobs.store import JobStore

        store = Store()
        service = ApprovalService(store.sf, JobStore(store.sf))

    if args.cmd == "list":
        rows = service.pending(sender=args.sender, kind=args.kind, rule_id=args.rule)
        for r in rows:
            out(_fmt(r))
        out(f"{len(rows)} pendente(s)")
        return 0

    if not (args.id or args.sender or args.kind or args.rule is not None):
        parser.error("informe --id, --sender, --rule ou --kind")
    rows = service.pending(ids=args.id, sender=args.sender, kind=args.kind, rule_id=args.rule)
    if not rows:
        out("nenhuma ação pendente com esse filtro")
        return 0
    for r in rows:
        out(_fmt(r))
    verb = "aprovar" if args.cmd == "approve" else "recusar"
    if not args.yes and ask(f"{verb} {len(rows)} ação(ões)? [s/N] ").strip().lower() not in (
        "s", "sim", "y", "yes",
    ):
        out("cancelado")
        return 1
    ids = [r.id for r in rows]
    if args.cmd == "approve":
        n = service.approve(ids, comment=args.comment)
        out(f"{n} aprovada(s); execução enfileirada")
    else:
        n = service.reject(ids, comment=args.comment)
        out(f"{n} recusada(s)" + (" ; comentário salvo na KB" if args.comment else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
