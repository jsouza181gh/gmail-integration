"""/api/auth: password (or Google, see google_entry) -> second factor -> cookies; refresh, logout, sessions."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.deps import (
    MFA_COOKIE,
    REFRESH_COOKIE,
    get_deps,
    request_ip,
    require_user,
)
from src.api.session_cookies import clear_mfa, clear_session, set_mfa, set_session
from src.auth.crypto import read_mfa
from src.auth.onboarding import is_complete
from src.db.models import User
from src.llm.types import Schema

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(Schema):
    username: str
    password: str


class SecondFactorIn(Schema):
    code: str


class MeOut(Schema):
    username: str
    role: str
    onboarding_step: str | None
    onboarding_done: bool


class SessionOut(Schema):
    family_id: str
    started_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


@router.post("/login", status_code=204)
def login(body: LoginIn, request: Request, response: Response) -> None:
    deps = get_deps(request)
    step = deps.auth.password_step(body.username[:64], body.password[:256], request_ip(request))
    if step.blocked:
        raise HTTPException(423, "login bloqueado")
    if not step.ok or step.user_id is None:
        raise HTTPException(401, "credenciais inválidas")
    set_mfa(response, deps, step.user_id)


@router.post("/2fa", status_code=204)
def second_factor(body: SecondFactorIn, request: Request, response: Response) -> None:
    deps = get_deps(request)
    ip = request_ip(request)
    token = request.cookies.get(MFA_COOKIE)
    user_id = read_mfa(token, deps.settings, deps.clock()) if token else None
    if user_id is None:
        raise HTTPException(401, "faça o login de novo")
    issued = deps.auth.second_step(user_id, body.code[:64], ip, request.headers.get("user-agent", ""))
    if issued is None:
        if deps.auth.check_open(ip) != "ok":
            raise HTTPException(423, "login bloqueado")
        raise HTTPException(401, "código inválido")
    clear_mfa(response)
    set_session(response, deps, issued)


@router.post("/refresh", status_code=204)
def refresh(request: Request, response: Response) -> Response:
    deps = get_deps(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    issued = (
        deps.auth.refresh(raw, request_ip(request), request.headers.get("user-agent", ""))
        if raw else None
    )
    if issued is None:
        denied = Response(status_code=401)
        clear_session(denied)
        return denied
    set_session(response, deps, issued)
    response.status_code = 204
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        get_deps(request).auth.logout(raw)
    clear_session(response)


@router.get("/me")
def me(request: Request, user_id: int = Depends(require_user)) -> MeOut:
    with get_deps(request).sf() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(401, "usuário não existe")
        return MeOut(username=user.username, role=user.role,
                     onboarding_step=user.onboarding_step,
                     onboarding_done=is_complete(user.onboarding_step))


@router.get("/sessions")
def sessions(request: Request, user_id: int = Depends(require_user)) -> list[SessionOut]:
    deps = get_deps(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    current = deps.auth.family_of(raw) if raw else None
    return [
        SessionOut(**row.model_dump(), current=row.family_id == current)
        for row in deps.auth.sessions(user_id)
    ]


@router.delete("/sessions/{family_id}", status_code=204)
def revoke(family_id: str, request: Request, user_id: int = Depends(require_user)) -> None:
    if not get_deps(request).auth.revoke_family(user_id, family_id[:36]):
        raise HTTPException(404, "sessão não encontrada")

