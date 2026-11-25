"""The user's Gemini keys as the panel shows and replaces them (never the key itself)."""
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from src.db.models import UserLlmKey
from src.llm.types import Schema
from src.platform.secrets import seal


class KeysStatus(Schema):
    configured: bool
    has_backfill_key: bool
    validated_at: datetime | None
    last_error: str | None


def keys_status(sf: sessionmaker[Session], user_id: int) -> KeysStatus:
    with sf() as s:
        row = s.get(UserLlmKey, user_id)
        if row is None:
            return KeysStatus(configured=False, has_backfill_key=False, validated_at=None,
                              last_error=None)
        return KeysStatus(configured=True, has_backfill_key=bool(row.backfill_key_enc),
                          validated_at=row.validated_at, last_error=row.last_error)


def replace_keys(
    sf: sessionmaker[Session], user_id: int, now: datetime, *,
    app_key: str | None, backfill_key: str | None, drop_backfill: bool,
) -> None:
    """Store keys the caller already validated; a clean row clears the old error."""
    with sf.begin() as s:
        row = s.get(UserLlmKey, user_id)
        if row is None:
            if app_key is None:
                raise ValueError("falta a chave principal")
            row = UserLlmKey(user_id=user_id, app_key_enc=seal(app_key))
            s.add(row)
        elif app_key is not None:
            row.app_key_enc = seal(app_key)
        if backfill_key is not None:
            row.backfill_key_enc = seal(backfill_key)
        elif drop_backfill:
            row.backfill_key_enc = None
        row.validated_at, row.last_error = now, None
