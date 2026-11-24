"""CLI: desbloqueio do login (só na VPS).

Uso:
  docker compose exec api python -m src.cli.unlock --list
  docker compose exec api python -m src.cli.unlock --ip 203.0.113.7
  docker compose exec api python -m src.cli.unlock --user pessoa@example.com
"""
import argparse
import sys

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    from src.alerts.service import build_alert_service
    from src.auth.config import load_auth_settings
    from src.auth.security_log import SecurityLog
    from src.auth.service import AuthService
    from src.db.store import Store

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ip")
    group.add_argument("--user", help="e-mail (ou usuário) a reabrir depois de falhas de vários IPs")
    group.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    store = Store()
    try:
        svc = AuthService(
            store.sf, load_auth_settings(), SecurityLog(store.sf, build_alert_service(store))
        )
        if args.list:
            for _, who, since in svc.locked_users():
                print(f"  usuário {who} FECHADO desde {since:%Y-%m-%d %H:%M}")
            for ip, reason, since in svc.blocks():
                print(f"  {ip}  desde {since:%Y-%m-%d %H:%M}  ({reason})")
        elif args.user:
            user_id = svc.find_user(args.user)
            if user_id is not None and svc.unlock_user(user_id):
                print(f"{args.user} reaberto")
            else:
                print(f"{args.user} não estava fechado")
        else:
            print(f"{args.ip} desbloqueado" if svc.unlock_ip(args.ip) else f"{args.ip} não estava bloqueado")
    finally:
        store.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
