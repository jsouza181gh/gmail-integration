"""Second-factor enrollment pieces shared by onboarding and the tests:
TOTP secret, recovery codes and the password rule."""
import secrets
from datetime import datetime

import pyotp
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.auth.config import AuthSettings
from src.auth.crypto import decrypt, encrypt, token_hash
from src.db.models import RecoveryCode, User
from src.llm.types import Schema

PASSWORD_MIN = 12
RECOVERY_CODES = 10
ISSUER = "Inbox Agent"


class Enrollment(Schema):
    otpauth_uri: str
    recovery_codes: tuple[str, ...]


def normalize_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum() and ch.isascii())


def check_password(password: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"A senha precisa de pelo menos {PASSWORD_MIN} caracteres.")


def new_recovery_code() -> str:
    raw = secrets.token_hex(6)  # 48 bits, shown as xxxx-xxxx-xxxx
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def begin_totp(user: User, settings: AuthSettings) -> str:
    """Store a fresh secret for the user (replacing any pending one); returns the otpauth URI."""
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt(secret, settings.app_secret)
    return pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name=ISSUER)


def pending_uri(user: User, settings: AuthSettings) -> str | None:
    if user.totp_secret_enc is None:
        return None
    secret = decrypt(user.totp_secret_enc, settings.app_secret)
    return pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name=ISSUER)


def totp_matches(user: User, settings: AuthSettings, code: str, now: datetime) -> bool:
    digits = code.strip().replace(" ", "")
    if user.totp_secret_enc is None or not (digits.isdigit() and len(digits) == 6):
        return False
    secret = decrypt(user.totp_secret_enc, settings.app_secret)
    return bool(pyotp.TOTP(secret).verify(digits, for_time=now, valid_window=1))


def issue_recovery_codes(s: Session, user_id: int) -> tuple[str, ...]:
    """Replace every recovery code of the user by 10 fresh ones (shown once)."""
    codes = tuple(new_recovery_code() for _ in range(RECOVERY_CODES))
    s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    s.add_all(RecoveryCode(user_id=user_id, code_hash=token_hash(normalize_recovery(c))) for c in codes)
    return codes
