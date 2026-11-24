"""Cookie plumbing shared by the auth routes and the Google callback handlers."""
from fastapi import Response

from src.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    MFA_COOKIE,
    ONB_COOKIE,
    REFRESH_COOKIE,
    ApiDeps,
)
from src.auth.crypto import issue_mfa, issue_onboarding, new_token
from src.auth.service import IssuedSession


def set_cookie(
    resp: Response, name: str, value: str, max_age: int, path: str, http_only: bool = True
) -> None:
    resp.set_cookie(
        name, value, max_age=max_age, path=path, secure=True, httponly=http_only, samesite="strict"
    )


def set_session(resp: Response, deps: ApiDeps, issued: IssuedSession) -> None:
    s = deps.settings
    set_cookie(resp, ACCESS_COOKIE, issued.access, s.access_ttl_s, "/api")
    set_cookie(resp, REFRESH_COOKIE, issued.refresh, s.refresh_ttl_s, "/api/auth")
    set_cookie(resp, CSRF_COOKIE, issued.csrf, s.refresh_ttl_s, "/", http_only=False)


def set_mfa(resp: Response, deps: ApiDeps, user_id: int) -> None:
    set_cookie(resp, MFA_COOKIE, issue_mfa(user_id, deps.settings, deps.clock()),
               deps.settings.mfa_ttl_s, "/api/auth")


def clear_mfa(resp: Response) -> None:
    resp.delete_cookie(MFA_COOKIE, path="/api/auth", secure=True, httponly=True, samesite="strict")


def set_onboarding(resp: Response, deps: ApiDeps, user_id: int) -> None:
    """Google proved who this is, no second factor yet: the cookie opens only
    /api/onboarding. It brings its own CSRF cookie (there is no session yet)."""
    ttl = deps.settings.onboarding_ttl_s
    set_cookie(resp, ONB_COOKIE, issue_onboarding(user_id, deps.settings, deps.clock()), ttl, "/api")
    set_cookie(resp, CSRF_COOKIE, new_token(), ttl, "/", http_only=False)


def clear_onboarding(resp: Response) -> None:
    resp.delete_cookie(ONB_COOKIE, path="/api", secure=True, httponly=True, samesite="strict")


def clear_session(resp: Response) -> None:
    for name, path in ((ACCESS_COOKIE, "/api"), (REFRESH_COOKIE, "/api/auth"),
                       (MFA_COOKIE, "/api/auth"), (ONB_COOKIE, "/api"), (CSRF_COOKIE, "/")):
        resp.delete_cookie(name, path=path, secure=True, httponly=name != CSRF_COOKIE, samesite="strict")
