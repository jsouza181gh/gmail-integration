"""/api/oauth/google: connect (reconnect) the user's Gmail and the shared callback.

`connect` needs a logged-in user; the callback is a browser redirect from
Google and is dispatched by the purpose carried in the encrypted `state`
(login and signup register their own handlers in the callback registry).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from src.api.deps import ApiDeps, get_deps, require_user
from src.db.accounts import account_for_user
from src.gmail.accounts_store import account_email, save_connection
from src.gmail.oauth import (
    GMAIL_MODIFY,
    NONCE_COOKIE,
    NONCE_PATH,
    GoogleIdentity,
    OAuthError,
    OAuthNotConfigured,
    OAuthState,
    callback_for,
    exchange,
    read_state,
    register_callback,
    same_browser,
    start_flow,
)
from src.gmail.oauth_clients import platform_client
from src.llm.types import Schema
from src.utils.console import log

router = APIRouter(prefix="/api/oauth/google", tags=["oauth"])
GMAIL_PAGE = "/seguranca"


class ConnectOut(Schema):
    url: str


def _redirect(deps: ApiDeps, path: str, result: str) -> Response:
    key = "gmail" if path == GMAIL_PAGE else "oauth"
    return RedirectResponse(f"{deps.settings.origin}{path}?{key}={result}", status_code=303)


@router.post("/connect")
def connect(response: Response, user_id: int = Depends(require_user)) -> ConnectOut:
    """Where to send the user to (re)connect their Gmail."""
    try:
        return ConnectOut(url=start_flow(response, "connect", user_id=user_id, invite_token=None))
    except OAuthNotConfigured as exc:
        raise HTTPException(503, "A conexão com o Google ainda não foi configurada.") from exc


def _connect_callback(state: OAuthState, identity: GoogleIdentity, deps: ApiDeps) -> Response:
    """Reconnect: the Google account must be the one this account was created with."""
    account = account_for_user(deps.sf, state.user_id) if state.user_id is not None else None
    expected = account_email(deps.sf, account) if account is not None else None
    if account is None or expected is None:
        return _redirect(deps, GMAIL_PAGE, "erro")
    if not identity.email_verified or identity.email != expected:
        return _redirect(deps, GMAIL_PAGE, "outro-email")
    if identity.refresh_token is None:
        return _redirect(deps, GMAIL_PAGE, "sem-token")
    if GMAIL_MODIFY not in identity.scopes:
        return _redirect(deps, GMAIL_PAGE, "sem-permissao")
    client = platform_client(deps.sf)
    if client is None:
        return _redirect(deps, GMAIL_PAGE, "erro")
    save_connection(deps.sf, account, identity, client.id)
    try:
        deps.on_connected(account)
    except Exception as exc:  # noqa: BLE001 - connected is saved; the renewal loop retries the watch
        log(f"watch após reconexão falhou: {type(exc).__name__}: {exc}")
    return _redirect(deps, GMAIL_PAGE, "ok")


register_callback("connect", _connect_callback)


@router.get("/callback")
def callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    deps = get_deps(request)
    if state is None:
        return _redirect(deps, "/", "erro")
    try:
        purpose = read_state(state).purpose
    except OAuthError:
        return _redirect(deps, "/", "erro")
    page = GMAIL_PAGE if purpose == "connect" else "/"
    if error is not None or code is None:  # the user said no at Google
        return _redirect(deps, page, "cancelado")
    if not same_browser(read_state(state), request.cookies.get(NONCE_COOKIE)):
        log("oauth google: state de outro navegador (nonce não confere)")
        return _redirect(deps, page, "erro")
    try:
        parsed, identity = exchange(code, state)
    except OAuthError as exc:
        log(f"oauth google: {exc}")
        return _redirect(deps, page, "erro")
    handler = callback_for(parsed.purpose)
    if handler is None:
        return _redirect(deps, page, "erro")
    resp = handler(parsed, identity, deps)
    resp.delete_cookie(NONCE_COOKIE, path=NONCE_PATH, secure=True, httponly=True, samesite="lax")
    return resp
