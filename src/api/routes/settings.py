"""/api/settings, /api/warnings, credentials and the last onboarding step.

Everything here is about the logged-in user; nothing reads another user's data.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from src.accounts import finish
from src.accounts.llm_keys import KeysStatus, keys_status, replace_keys
from src.api.deps import REFRESH_COOKIE, get_deps, require_user
from src.api.routes.ops import start_backfill
from src.auth.service import NewTotp
from src.db.accounts import account_for_user
from src.db.models import GmailAccount, User, UserLlmKey
from src.db.scope import system_scope
from src.llm.keys import validate_key
from src.llm.types import Schema

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)], tags=["settings"])

WarningKind = Literal["no_gmail", "gmail_revoked", "gmail_disconnected", "key_missing", "key_invalid"]


class SettingsOut(Schema):
    username: str
    email: str | None
    role: str
    has_password: bool
    has_totp: bool
    gmail_email: str | None
    llm_keys: KeysStatus


class LlmKeysIn(Schema):
    app_key: str | None = None
    backfill_key: str | None = None
    drop_backfill: bool = False


class PasswordIn(Schema):
    current_password: str = ""
    new_password: str


class TotpIn(Schema):
    password: str = ""
    code: str


class WarningOut(Schema):
    kind: WarningKind


class FinishIn(Schema):
    backfill: bool


class FinishOut(Schema):
    backfill_started: bool


class EndedOut(Schema):
    ended: int


def _user(request: Request, user_id: int) -> User:
    with get_deps(request).sf() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(401, "usuário não existe")
        s.expunge(user)
        return user


def _current_family(request: Request) -> str | None:
    raw = request.cookies.get(REFRESH_COOKIE)
    return get_deps(request).auth.family_of(raw) if raw else None


@router.get("/settings")
def settings(request: Request, user_id: int = Depends(require_user)) -> SettingsOut:
    deps = get_deps(request)
    user = _user(request, user_id)
    with system_scope(), deps.sf() as s:
        gmail = s.query(GmailAccount.email_address).filter(GmailAccount.user_id == user_id).scalar()
    return SettingsOut(
        username=user.username, email=user.email, role=user.role,
        has_password=bool(user.password_hash), has_totp=bool(user.totp_secret_enc),
        gmail_email=gmail, llm_keys=keys_status(deps.sf, user_id),
    )


@router.put("/settings/llm-keys")
def replace_llm_keys(
    body: LlmKeysIn, request: Request, user_id: int = Depends(require_user)
) -> KeysStatus:
    deps = get_deps(request)
    app_key = body.app_key.strip() if body.app_key else None
    backfill_key = body.backfill_key.strip() if body.backfill_key else None
    for key in (app_key, backfill_key):
        problem = validate_key(key) if key else None
        if problem:
            raise HTTPException(422, problem)
    try:
        replace_keys(deps.sf, user_id, deps.clock(), app_key=app_key,
                     backfill_key=backfill_key, drop_backfill=body.drop_backfill)
    except ValueError as exc:
        raise HTTPException(422, "Cole a chave principal do Gemini.") from exc
    return keys_status(deps.sf, user_id)


@router.post("/auth/password", status_code=204)
def change_password(
    body: PasswordIn, request: Request, user_id: int = Depends(require_user)
) -> None:
    result = get_deps(request).auth.change_password(
        user_id, body.current_password[:256], body.new_password[:256], _current_family(request)
    )
    if result == "weak":
        raise HTTPException(422, "A senha precisa de pelo menos 12 caracteres.")
    if result == "wrong_password":
        raise HTTPException(403, "A senha atual não confere.")


@router.post("/auth/totp")
def change_totp(body: TotpIn, request: Request, user_id: int = Depends(require_user)) -> NewTotp:
    issued = get_deps(request).auth.rotate_totp(
        user_id, body.password[:256], body.code[:16], _current_family(request)
    )
    if issued is None:
        raise HTTPException(403, "A senha ou o código não conferem.")
    return issued


@router.delete("/auth/sessions")
def sign_out_others(request: Request, user_id: int = Depends(require_user)) -> EndedOut:
    ended = get_deps(request).auth.revoke_others(user_id, _current_family(request))
    return EndedOut(ended=ended)


@router.get("/warnings")
def warnings(request: Request, user_id: int = Depends(require_user)) -> list[WarningOut]:
    deps = get_deps(request)
    if account_for_user(deps.sf, user_id) is None:
        return [WarningOut(kind="no_gmail")]
    out: list[WarningOut] = []
    with system_scope(), deps.sf() as s:
        status = s.query(GmailAccount.status).filter(GmailAccount.user_id == user_id).scalar()
        key = s.get(UserLlmKey, user_id)
    if status == "revoked":
        out.append(WarningOut(kind="gmail_revoked"))
    elif status == "disconnected":
        out.append(WarningOut(kind="gmail_disconnected"))
    if key is None:
        out.append(WarningOut(kind="key_missing"))
    elif key.last_error is not None:
        out.append(WarningOut(kind="key_invalid"))
    return out


@router.post("/onboarding/finish")
def finish_onboarding(
    body: FinishIn, request: Request, user_id: int = Depends(require_user)
) -> FinishOut:
    deps = get_deps(request)
    if not finish.can_finish(_user(request, user_id).onboarding_step):
        raise HTTPException(409, "Termine os passos anteriores primeiro.")
    if body.backfill:
        start_backfill(deps)
    finish.complete(deps.sf, user_id)
    return FinishOut(backfill_started=body.backfill)
