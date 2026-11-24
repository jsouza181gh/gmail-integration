"""Secrets at rest: Fernet over APP_SECRET, with rotation.

APP_SECRET may hold several keys separated by commas: the first encrypts,
all of them decrypt (`MultiFernet`), so a key can be rotated without
re-encrypting everything at once. Read at call time, never cached.
"""
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def _fernet() -> MultiFernet:
    raw = os.getenv("APP_SECRET", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("APP_SECRET não definida (veja .env.example)")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def seal(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def unseal(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("segredo não decifra com APP_SECRET atual") from exc
