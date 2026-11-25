"""Google OAuth (web flow) for the platform's own client.

PKCE (S256); the `state` is an encrypted, short-lived `OAuthState` that
carries the purpose, the user it was issued to and the PKCE verifier, so
nothing is kept on the server between the redirect and the callback.
Callbacks are dispatched by purpose through a registry: this phase registers
`connect`; the login/signup flows register theirs.

The Google calls go through one injectable function (`HttpForm`), so tests
never reach Google.
"""
import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from src.gmail.oauth_clients import OAuthClientRow, platform_client
from src.platform.secrets import seal, unseal

if TYPE_CHECKING:
    from src.api.deps import ApiDeps

OAuthPurpose = Literal["connect", "login", "signup"]

GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SETTINGS = "https://www.googleapis.com/auth/gmail.settings.basic"
GMAIL_SCOPES = ["openid", "email", "profile", GMAIL_MODIFY, GMAIL_SETTINGS]
LOGIN_SCOPES = ["openid", "email"]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALLBACK_PATH = "/api/oauth/google/callback"
STATE_TTL_S = 600
# Binds a state to the browser that started it (login CSRF): the nonce travels
# both inside the sealed state and in this cookie, and the callback compares them.
NONCE_COOKIE = "oauth_nonce"
NONCE_PATH = "/api/oauth/google"

# (url, form fields) -> (HTTP status, body)
type HttpForm = Callable[[str, dict[str, str]], tuple[int, str]]


class OAuthError(RuntimeError):
    """The flow cannot continue; the message is safe to show a layperson."""


class OAuthNotConfigured(OAuthError):
    pass


class OAuthState(BaseModel):
    purpose: OAuthPurpose
    user_id: int | None
    verifier: str
    invite_token: str | None
    nonce: str = ""
    exp: int


class GoogleIdentity(BaseModel):
    email: str  # lowercase
    email_verified: bool
    sub: str
    refresh_token: str | None
    scopes: list[str]


class _TokenResponse(BaseModel):
    id_token: str
    refresh_token: str | None = None
    scope: str = ""


class _IdClaims(BaseModel):
    email: str
    email_verified: bool = False
    sub: str
    aud: str


def post_form(url: str, data: dict[str, str]) -> tuple[int, str]:
    response = httpx.post(url, data=data, timeout=20.0)
    return response.status_code, response.text


class _Runtime:
    def __init__(self, sf: sessionmaker[Session], origin: str, http: HttpForm) -> None:
        self.sf = sf
        self.origin = origin.rstrip("/")
        self.http = http


type CallbackHandler = Callable[[OAuthState, GoogleIdentity, ApiDeps], Response]

_runtime: _Runtime | None = None
_callbacks: dict[OAuthPurpose, CallbackHandler] = {}



def configure(sf: sessionmaker[Session], origin: str, http: HttpForm | None = None) -> None:
    """Called once by the API at start-up (tests pass a fake `http`)."""
    global _runtime
    _runtime = _Runtime(sf, origin, http or post_form)


def _rt() -> _Runtime:
    if _runtime is None:
        raise OAuthNotConfigured("o login com Google ainda não foi configurado")
    return _runtime


def redirect_uri() -> str:
    return f"{_rt().origin}{CALLBACK_PATH}"


def register_callback(purpose: OAuthPurpose, handler: CallbackHandler) -> None:
    _callbacks[purpose] = handler


def callback_for(purpose: OAuthPurpose) -> CallbackHandler | None:
    return _callbacks.get(purpose)


def _client() -> OAuthClientRow:
    client = platform_client(_rt().sf)
    if client is None:
        raise OAuthNotConfigured("o login com Google ainda não foi configurado")
    return client


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def authorization_url(
    purpose: OAuthPurpose, *, user_id: int | None, invite_token: str | None, nonce: str = ""
) -> str:
    client = _client()
    verifier = secrets.token_urlsafe(64)
    state = OAuthState(
        purpose=purpose, user_id=user_id, verifier=verifier, invite_token=invite_token, nonce=nonce,
        exp=int(time.time()) + STATE_TTL_S,
    )
    login = purpose == "login"
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(LOGIN_SCOPES if login else GMAIL_SCOPES),
        "state": seal(state.model_dump_json()),
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    if login:
        params["prompt"] = "select_account"
    else:
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    return f"{AUTH_URL}?{urlencode(params)}"


def start_flow(
    resp: Response, purpose: OAuthPurpose, *, user_id: int | None, invite_token: str | None
) -> str:
    """The Google URL for `purpose`, with its nonce cookie set on `resp`.

    Lax, not strict: the callback is a top-level redirect from Google."""
    nonce = new_nonce()
    url = authorization_url(purpose, user_id=user_id, invite_token=invite_token, nonce=nonce)
    resp.set_cookie(NONCE_COOKIE, nonce, max_age=STATE_TTL_S, path=NONCE_PATH,
                    secure=True, httponly=True, samesite="lax")
    return url


def same_browser(state: OAuthState, cookie: str | None) -> bool:
    return bool(state.nonce) and cookie is not None and hmac.compare_digest(
        state.nonce.encode(), cookie.encode()
    )


def read_state(raw: str) -> OAuthState:
    """Decrypt and validate a `state`; expired or forged ones are refused."""
    try:
        state = OAuthState.model_validate_json(unseal(raw))
    except (ValueError, ValidationError) as exc:  # unseal: ValueError; pydantic: ValueError
        raise OAuthError("o link de autorização é inválido") from exc
    if state.exp < int(time.time()):
        raise OAuthError("o link de autorização expirou; tente de novo")
    return state


def exchange(code: str, state: str) -> tuple[OAuthState, GoogleIdentity]:
    """Trade the authorization code for the identity (and refresh token)."""
    rt = _rt()
    parsed = read_state(state)
    client = _client()
    status, body = rt.http(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect_uri(),
            "code_verifier": parsed.verifier,
        },
    )
    if status != 200:
        raise OAuthError("o Google recusou o código de autorização")
    try:
        token = _TokenResponse.model_validate_json(body)
        # Straight from Google's token endpoint over TLS: the signature check
        # is optional per OIDC core 3.1.3.7; the audience is still verified.
        claims = _IdClaims.model_validate(
            jwt.decode(token.id_token, options={"verify_signature": False})
        )
    except (ValidationError, jwt.PyJWTError) as exc:
        raise OAuthError("resposta inesperada do Google") from exc
    if claims.aud != client.client_id:
        raise OAuthError("resposta do Google para outro aplicativo")
    return parsed, GoogleIdentity(
        email=claims.email.strip().lower(),
        email_verified=claims.email_verified,
        sub=claims.sub,
        refresh_token=token.refresh_token,
        scopes=token.scope.split(),
    )
