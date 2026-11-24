"""STUB(fase 4): substituído no merge. Chaves do Gemini por conta."""
from sqlalchemy.orm import Session, sessionmaker

from src.llm.types import Schema


class AccountKeys(Schema):
    app_key: str
    backfill_key: str | None = None


def keys_for_account(sf: sessionmaker[Session], account_id: int) -> AccountKeys | None:
    return None


def validate_key(key: str) -> str | None:
    """None = ok; text = what to tell a lay person."""
    if not key.strip().startswith("AIza") or len(key.strip()) < 30:
        return "Essa chave não parece ser do Gemini. Ela começa com AIza."
    return None
