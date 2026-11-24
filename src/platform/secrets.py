"""Sealing of secrets at rest with the platform key (APP_SECRET)."""
# STUB(fase 2): substituído no merge
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def _fernet() -> MultiFernet:
    keys = [k.strip() for k in os.environ["APP_SECRET"].split(",") if k.strip()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def seal(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def unseal(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("segredo não decifra com APP_SECRET atual") from exc
