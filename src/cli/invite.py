"""CLI: convida uma pessoa pelo e-mail e imprime o link para copiar (WhatsApp etc.).

Uso (na VPS):  docker compose exec api python -m src.cli.invite <email> [--by <email do admin>]

Substitui o antigo create_user: a pessoa entra pelo link, com o Google, e
cadastra o próprio 2FA. O primeiro admin (João) vem da migração 0021.
"""
import argparse
import sys

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.auth.invites import InviteError, create_invite, normalize_email
from src.db.models import User


def invite(sf: sessionmaker[Session], email: str, by: str | None) -> str:
    """Create the invite as an admin (the named one, else the first); returns the link."""
    with sf() as s:
        query = select(User).where(User.role == "admin").order_by(User.id)
        if by is not None:
            query = query.where(User.email == normalize_email(by))
        admin = s.scalars(query).first()
        admin_id = admin.id if admin is not None else None
    if admin_id is None:
        raise InviteError("nenhum administrador encontrado para assinar o convite")
    return create_invite(sf, email, admin_id)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    from src.db.store import Store

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("email")
    parser.add_argument("--by", default=None, help="e-mail do admin que convida (padrão: o primeiro)")
    args = parser.parse_args(argv)
    store = Store()
    try:
        link = invite(store.sf, args.email, args.by)
    except InviteError as exc:
        sys.exit(str(exc))
    finally:
        store.close()
    print(f"\nConvite para {args.email.strip().lower()}. Mande este link (vale uma vez):\n\n  {link}\n")


if __name__ == "__main__":
    main()
