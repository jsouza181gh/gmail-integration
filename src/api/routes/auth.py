"""/api/auth: password -> second factor -> cookies; refresh, logout, sessions."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    MFA_COOKIE,
    REFRESH_COOKIE,
    ApiDeps,
    get_deps,
    request_ip,
    require_user,
)
from src.auth.crypto import issue_mfa, read_mfa
from src.auth.service import IssuedSession
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
    email: str | None
    onboarding_step: str | None


class SessionOut(Schema):
    family_id: str
    started_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


def _cookie(resp: Response, name: str, value: str, max_age: int, path: str, http_only: bool = True) -> None:
    resp.set_cookie(
        name, value, max_age=max_age, path=path, secure=True, httponly=http_only, samesite="strict"
    )


def _set_session(resp: Response, deps: ApiDeps, issued: IssuedSession) -> None:
    s = deps.settings
    _cookie(resp, ACCESS_COOKIE, issued.access, s.access_ttl_s, "/api")
    _cookie(resp, REFRESH_COOKIE, issued.refresh, s.refresh_ttl_s, "/api/auth")
    _cookie(resp, CSRF_COOKIE, issued.csrf, s.refresh_ttl_s, "/", http_only=False)


def _clear_session(resp: Response) -> None:
    for name, path in ((ACCESS_COOKIE, "/api"), (REFRESH_COOKIE, "/api/auth"),
                       (MFA_COOKIE, "/api/auth"), (CSRF_COOKIE, "/")):
        resp.delete_cookie(name, path=path, secure=True, httponly=name != CSRF_COOKIE, samesite="strict")


@router.post("/login", status_code=204)
def login(body: LoginIn, request: Request, response: Response) -> None:
    deps = get_deps(request)
    step = deps.auth.password_step(body.username[:64], body.password[:256], request_ip(request))
    if step.blocked:
        raise HTTPException(423, "login bloqueado")
    if not step.ok or step.user_id is None:
        raise HTTPException(401, "credenciais inválidas")
    _cookie(response, MFA_COOKIE, issue_mfa(step.user_id, deps.settings, deps.clock()),
            deps.settings.mfa_ttl_s, "/api/auth")


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
    response.delete_cookie(MFA_COOKIE, path="/api/auth", secure=True, httponly=True, samesite="strict")
    _set_session(response, deps, issued)


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
        _clear_session(denied)
        return denied
    _set_session(response, deps, issued)
    response.status_code = 204
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        get_deps(request).auth.logout(raw)
    _clear_session(response)


@router.get("/me")
def me(request: Request, user_id: int = Depends(require_user)) -> MeOut:
    with get_deps(request).sf() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(401, "usuário não existe")
        return MeOut(username=user.username, role=user.role, email=user.email,
                     onboarding_step=user.onboarding_step)


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

