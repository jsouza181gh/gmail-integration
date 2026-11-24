"""STUB(fase 2): substituído no merge. Cifra segredos com a primeira chave de APP_SECRET."""
import os

from src.auth.crypto import decrypt, encrypt


def _key() -> str:
    return os.environ["APP_SECRET"].split(",")[0].strip()


def seal(plain: str) -> str:
    return encrypt(plain, _key())


def unseal(token: str) -> str:
    return decrypt(token, _key())  # ValueError on a bad token
