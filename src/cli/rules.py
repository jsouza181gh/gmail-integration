"""CLI: regras de automação (condição determinística -> ação, sem LLM).

Uso:
  python -m src.cli.rules add --name N [--sender A] [--domain D] [--category C] [--subject S] --action K
  python -m src.cli.rules list
  python -m src.cli.rules promote --id N [--yes]   # suggest -> auto (nunca para block_sender)
  python -m src.cli.rules disable --id N

Regras nascem em `suggest`: o que disparam vai para a fila de aprovação.
"""
import argparse
from collections.abc import Callable, Sequence

from dotenv import load_dotenv

from src.agent.rules import RuleAdmin
from src.db.rows import RuleRow

Out = Callable[[str], None]
Ask = Callable[[str], str]


def _conditions(r: RuleRow) -> str:
    parts = [
        f"remetente={r.sender_address}" if r.sender_address else "",
        f"domínio={r.sender_domain}" if r.sender_domain else "",
        f"categoria={r.category}" if r.category else "",
        f"assunto~{r.subject_contains}" if r.subject_contains else "",
    ]
    return " E ".join(p for p in parts if p)


def _fmt(r: RuleRow) -> str:
    state = r.mode if r.active else "inativa"
    return (
        f"#{r.id:<4} {r.name[:30]:<30} {state:<8} {_conditions(r)} -> {r.action} "
        f"(disparos {r.fired_count}, aprovadas {r.approved_count}, recusadas {r.rejected_count})"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.cli.rules", description="regras de automação")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--sender")
    add.add_argument("--domain")
    add.add_argument("--category")
    add.add_argument("--subject")
    add.add_argument("--action", required=True)
    sub.add_parser("list")
    promote = sub.add_parser("promote")
    promote.add_argument("--id", type=int, required=True)
    promote.add_argument("--yes", action="store_true")
    disable = sub.add_parser("disable")
    disable.add_argument("--id", type=int, required=True)
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    admin: RuleAdmin | None = None,
    out: Out = print,
    ask: Ask = input,
) -> int:
    args = _parser().parse_args(argv)
    if admin is None:
        load_dotenv()
        from src.db.store import Store

        admin = RuleAdmin(Store().sf)

    try:
        if args.cmd == "add":
            rid = admin.add(
                name=args.name,
                action=args.action,
                sender_address=args.sender,
                sender_domain=args.domain,
                category=args.category,
                subject_contains=args.subject,
            )
            out(f"regra #{rid} criada em modo suggest")
        elif args.cmd == "list":
            for r in admin.list():
                out(_fmt(r))
        elif args.cmd == "promote":
            rule = admin.get(args.id)
            if rule is None:
                raise ValueError(f"regra #{args.id} não existe")
            out(_fmt(rule))
            if not args.yes and ask("promover para auto? [s/N] ").strip().lower() not in (
                "s", "sim", "y", "yes",
            ):
                out("cancelado")
                return 1
            admin.promote(args.id)
            out(f"regra #{args.id} agora roda em auto")
        elif args.cmd == "disable":
            admin.disable(args.id)
            out(f"regra #{args.id} desativada")
    except ValueError as exc:
        out(f"erro: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
