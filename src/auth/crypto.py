"""Password hashing, secret encryption, opaque tokens and signed JWTs.

argon2id for the password; SHA-256 for high-entropy random tokens (refresh,
recovery codes), where a slow hash adds nothing; Fernet for the TOTP secret,
which must be readable again. JWTs are HS256 with a `typ` claim so an access
token can never be used as a download token or vice versa.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ValidationError

from src.auth.config import AuthSettings

TokenType = Literal["access", "mfa", "dl"]
_hasher = PasswordHasher()
_ALGORITHM = "HS256"


class _Claims(BaseModel):
    sub: str
    typ: str
    exp: int
    iat: int
    att: int | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


DUMMY_HASH = hash_password("dummy password for unknown users")


def encrypt(secret: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(secret.encode()).decode()


def decrypt(token: str, key: str) -> str:
    try:
        return Fernet(key.encode()).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("segredo não decifra com APP_SECRET atual") from exc


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _issue(
    typ: TokenType, subject: int, ttl_s: int, settings: AuthSettings, now: datetime,
    attachment: int | None = None,
) -> str:
    claims: dict[str, str | int] = {
        "sub": str(subject),
        "typ": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
    }
    if attachment is not None:
        claims["att"] = attachment
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALGORITHM)


def _read(token: str, typ: TokenType, settings: AuthSettings, now: datetime) -> _Claims | None:
    try:
        raw = jwt.decode(
            token, settings.jwt_secret, algorithms=[_ALGORITHM],
            options={"verify_exp": False, "require": ["sub", "typ", "exp", "iat"]},
        )
        claims = _Claims.model_validate(raw)
    except (jwt.PyJWTError, ValidationError):
        return None
    if claims.typ != typ or claims.exp <= int(now.timestamp()):
        return None
    return claims


def issue_access(user_id: int, settings: AuthSettings, now: datetime) -> str:
    return _issue("access", user_id, settings.access_ttl_s, settings, now)


def read_access(token: str, settings: AuthSettings, now: datetime) -> int | None:
    claims = _read(token, "access", settings, now)
    return int(claims.sub) if claims else None


def issue_mfa(user_id: int, settings: AuthSettings, now: datetime) -> str:
    return _issue("mfa", user_id, settings.mfa_ttl_s, settings, now)


def read_mfa(token: str, settings: AuthSettings, now: datetime) -> int | None:
    claims = _read(token, "mfa", settings, now)
    return int(claims.sub) if claims else None


def issue_download(attachment_id: int, settings: AuthSettings, now: datetime) -> str:
    return _issue("dl", 0, settings.download_ttl_s, settings, now, attachment=attachment_id)


def read_download(token: str, attachment_id: int, settings: AuthSettings, now: datetime) -> bool:
    claims = _read(token, "dl", settings, now)
    return claims is not None and claims.att == attachment_id
