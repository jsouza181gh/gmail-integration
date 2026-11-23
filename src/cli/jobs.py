"""CLI: estado da fila e reenfileiramento de jobs mortos.

Uso:
  python -m src.cli.jobs status
  python -m src.cli.jobs dead [--limit N] [--kind K]
  python -m src.cli.jobs requeue <id> [<id> ...]
  python -m src.cli.jobs requeue --all-dead [--kind K]
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.cli.account import add_account_arg, cli_account  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.jobs.store import JobStore  # noqa: E402

ALL_DEAD_LIMIT = 100_000


def status(jobs: JobStore) -> str:
    lines = [f"{'kind':<18}{'queued':>8}{'running':>9}{'done':>8}{'dead':>7}"]
    for kind, c in jobs.counts_by_kind().items():
        lines.append(f"{kind:<18}{c.queued:>8}{c.running:>9}{c.done:>8}{c.dead:>7}")
    return "\n".join(lines)


def dead(jobs: JobStore, limit: int, kind: str | None) -> str:
    rows = jobs.dead(limit, kind)
    if not rows:
        return "nenhum job morto"
    return "\n".join(
        f"#{d.id} {d.kind} tentativas={d.attempts} {(d.last_error or '')[:160]}" for d in rows
    )


def requeue(jobs: JobStore, ids: list[int], all_dead: bool, kind: str | None) -> str:
    if all_dead:
        ids = [d.id for d in jobs.dead(ALL_DEAD_LIMIT, kind)]
    done = [i for i in ids if jobs.requeue(i)]
    skipped = sorted(set(ids) - set(done))
    out = f"{len(done)} job(s) de volta à fila"
    return out + (f"; ignorados (não estavam mortos): {skipped}" if skipped else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_status = sub.add_parser("status")
    p_dead = sub.add_parser("dead")
    p_dead.add_argument("--limit", type=int, default=50)
    p_dead.add_argument("--kind")
    p_req = sub.add_parser("requeue")
    p_req.add_argument("ids", nargs="*", type=int)
    p_req.add_argument("--all-dead", action="store_true")
    p_req.add_argument("--kind")
    for sp in (p_status, p_dead, p_req):
        add_account_arg(sp)
    args = parser.parse_args(argv)

    store = Store()
    try:
        with cli_account(store.sf, args.account):
            jobs = JobStore(store.sf)
            if args.cmd == "status":
                print(status(jobs))
            elif args.cmd == "dead":
                print(dead(jobs, args.limit, args.kind))
            else:
                if not args.ids and not args.all_dead:
                    parser.error("informe ids ou --all-dead")
                print(requeue(jobs, args.ids, args.all_dead, args.kind))
    finally:
        store.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
