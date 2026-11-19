"""CLI: cria (ou recria) o usuário único do painel.

Uso (na VPS):  docker compose exec api python -m src.cli.create_user <usuario> [--reset]

Pede a senha (mín. 12 caracteres), mostra o QR do TOTP para o Google
Authenticator e 10 recovery codes de uso único — anote-os, não aparecem de novo.
--reset troca senha, TOTP e recovery codes e derruba todas as sessões.
"""
import argparse
import getpass
import secrets
import sys
from collections.abc import Callable
from datetime import UTC, datetime

import pyotp
from dotenv import load_dotenv
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.auth.config import AuthSettings
from src.auth.crypto import encrypt, hash_password, token_hash
from src.auth.service import normalize_recovery
from src.db.models import AuthSession, RecoveryCode, User
from src.llm.types import Schema

PASSWORD_MIN = 12
RECOVERY_CODES = 10
ISSUER = "gmail-integration"


class Enrollment(Schema):
    otpauth_uri: str
    recovery_codes: tuple[str, ...]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _recovery_code() -> str:
    raw = secrets.token_hex(6)  # 48 bits, shown as xxxx-xxxx-xxxx
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def create_user(
    sf: sessionmaker[Session],
    settings: AuthSettings,
    username: str,
    password: str,
    *,
    reset: bool,
    clock: Callable[[], datetime] = _utcnow,
) -> Enrollment:
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"senha precisa de pelo menos {PASSWORD_MIN} caracteres")
    now = clock()
    secret = pyotp.random_base32()
    codes = tuple(_recovery_code() for _ in range(RECOVERY_CODES))
    with sf.begin() as s:
        existing = s.scalars(select(User)).first()
        if existing is not None and not reset:
            raise RuntimeError("usuário já existe; use --reset para trocar as credenciais")
        if existing is None:
            user = User(username=username, created_at=now, password_hash="", totp_secret_enc="",
                        password_changed_at=now)
            s.add(user)
        else:
            user = existing
            user.username = username
            s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
            s.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason="reset")
            )
        user.password_hash = hash_password(password)
        user.totp_secret_enc = encrypt(secret, settings.app_secret)
        user.password_changed_at = now
        s.flush()
        s.add_all(
            RecoveryCode(user_id=user.id, code_hash=token_hash(normalize_recovery(c))) for c in codes
        )
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)
    return Enrollment(otpauth_uri=uri, recovery_codes=codes)


def _print_qr(uri: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    from src.auth.config import load_auth_settings
    from src.db.store import Store

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("username")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)

    password = getpass.getpass("senha: ")
    if password != getpass.getpass("repita a senha: "):
        sys.exit("as senhas não conferem")
    store = Store()
    try:
        enrollment = create_user(store.sf, load_auth_settings(), args.username, password,
                                 reset=args.reset)
    except (ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
    finally:
        store.close()
    print("\nEscaneie no app autenticador:\n")
    _print_qr(enrollment.otpauth_uri)
    print(f"\nOu digite a URI: {enrollment.otpauth_uri}\n")
    print("Recovery codes (uso único; guarde fora do servidor):")
    for code in enrollment.recovery_codes:
        print(f"  {code}")


if __name__ == "__main__":
    main()
