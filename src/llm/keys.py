"""Each user's own Gemini keys (BYOK): stored sealed in `user_llm_keys`.

Platform paths: they read across accounts (system scope) but only ever return
keys and key metadata, never content. `keys_for_account` is the one lookup the
LLM layer uses; a user with no usable key gets None and no LLM call is made on
their behalf (never another user's key, never a platform key).
"""
from datetime import UTC, datetime

from google import genai
from google.genai import types as genai_types
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailAccount, UserLlmKey
from src.db.scope import system_scope
from src.db.vector import EMBED_DIM
from src.llm.gateway import DEFAULT_EMBED_MODEL
from src.llm.types import Schema
from src.platform.secrets import seal, unseal
from src.utils.console import log


class AccountKeys(Schema):
    """app: chat, triage, new mail; backfill: the history backfill (optional)."""

    app: str
    backfill: str | None = None

    @property
    def separate(self) -> bool:
        return self.backfill is not None and self.backfill != self.app


class KeyStatus(Schema):
    """What the panel may know about a user's keys: never the keys themselves."""

    configured: bool
    has_backfill_key: bool
    validated_at: datetime | None
    last_error: str | None


def user_id_for_account(sf: sessionmaker[Session], account_id: int) -> int | None:
    with system_scope(), sf() as s:
        return s.scalar(select(GmailAccount.user_id).where(GmailAccount.id == account_id))


def keys_for_account(sf: sessionmaker[Session], account_id: int) -> AccountKeys | None:
    with system_scope(), sf() as s:
        row = s.scalars(
            select(UserLlmKey)
            .join(GmailAccount, GmailAccount.user_id == UserLlmKey.user_id)
            .where(GmailAccount.id == account_id)
        ).one_or_none()
        if row is None:
            return None
        try:
            app = unseal(row.app_key_enc)
            backfill = unseal(row.backfill_key_enc) if row.backfill_key_enc else None
        except ValueError:
            log(f"chave da conta {account_id} ilegível (APP_SECRET mudou?)")
            return None
    return AccountKeys(app=app, backfill=backfill)


def save_keys(
    sf: sessionmaker[Session], user_id: int, *, app_key: str, backfill_key: str | None,
    now: datetime | None = None,
) -> None:
    """Replace the user's keys (already validated by the caller)."""
    when = now or datetime.now(UTC)
    with system_scope(), sf.begin() as s:
        row = s.get(UserLlmKey, user_id)
        if row is None:
            row = UserLlmKey(user_id=user_id, app_key_enc="")
            s.add(row)
        row.app_key_enc = seal(app_key)
        row.backfill_key_enc = seal(backfill_key) if backfill_key else None
        row.validated_at = when
        row.last_error = None


def mark_key_error(sf: sessionmaker[Session], account_id: int, error: str) -> None:
    """The key stopped working (revoked, wrong): the panel warns the user."""
    with system_scope(), sf.begin() as s:
        user_id = s.scalar(select(GmailAccount.user_id).where(GmailAccount.id == account_id))
        row = s.get(UserLlmKey, user_id) if user_id is not None else None
        if row is not None:
            row.last_error = error[:500]


def key_status(sf: sessionmaker[Session], user_id: int) -> KeyStatus:
    with system_scope(), sf() as s:
        row = s.get(UserLlmKey, user_id)
        if row is None:
            return KeyStatus(configured=False, has_backfill_key=False, validated_at=None,
                             last_error=None)
        return KeyStatus(configured=True, has_backfill_key=row.backfill_key_enc is not None,
                         validated_at=row.validated_at, last_error=row.last_error)


def explain_key_error(raw: str) -> str:
    """Google's error text in words a layperson can act on."""
    if "API_KEY_INVALID" in raw or "API key not valid" in raw or "400" in raw:
        return (
            "Essa chave não existe ou foi copiada incompleta. Volte ao Google AI Studio, "
            "copie a chave inteira (ela começa com AIza) e cole de novo."
        )
    if "PERMISSION_DENIED" in raw or "403" in raw:
        return (
            "Essa chave não tem acesso ao Gemini. Crie uma chave nova no Google AI Studio, "
            "num projeto com o Gemini liberado, e cole aqui."
        )
    if "RESOURCE_EXHAUSTED" in raw or "429" in raw:
        return (
            "A chave é válida, mas a cota do dia dela acabou. Use outra chave ou tente "
            "de novo amanhã."
        )
    return "Não consegui falar com o Google agora. Espere um minuto e tente de novo."


def validate_key(key: str) -> str | None:
    """None = the key works; otherwise what to tell the user. One tiny real call
    (a one-word embedding): it also proves access to the model the platform uses."""
    try:
        client = genai.Client(api_key=key)
        client.models.embed_content(
            model=DEFAULT_EMBED_MODEL,
            contents="ok",
            config=genai_types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
        )
    except Exception as exc:  # noqa: BLE001 - every provider failure is explained below
        return explain_key_error(f"{type(exc).__name__}: {exc}")
    return None
