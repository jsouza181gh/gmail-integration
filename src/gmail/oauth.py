"""Google OAuth for the web flow: state, identity and the callback registry."""
# STUB(fase 2): substituído no merge
from collections.abc import Callable
from typing import Literal

from fastapi import Response
from pydantic import BaseModel

from src.api.deps import ApiDeps

OAuthPurpose = Literal["connect", "login", "signup"]
GMAIL_SCOPES = (
    "openid email profile https://www.googleapis.com/auth/gmail.modify"
    " https://www.googleapis.com/auth/gmail.settings.basic"
)
LOGIN_SCOPES = "openid email"


class OAuthState(BaseModel):
    purpose: OAuthPurpose
    user_id: int | None
    verifier: str
    invite_token: str | None
    exp: int


class GoogleIdentity(BaseModel):
    email: str  # lowercase
    email_verified: bool
    sub: str
    refresh_token: str | None
    scopes: list[str]


type CallbackHandler = Callable[[OAuthState, GoogleIdentity, ApiDeps], Response]

_CALLBACKS: dict[OAuthPurpose, CallbackHandler] = {}


def register_callback(purpose: OAuthPurpose, handler: CallbackHandler) -> None:
    _CALLBACKS[purpose] = handler


def authorization_url(
    purpose: OAuthPurpose, *, user_id: int | None, invite_token: str | None
) -> str:
    raise NotImplementedError("STUB(fase 2)")


def exchange(code: str, state: str) -> tuple[OAuthState, GoogleIdentity]:
    raise NotImplementedError("STUB(fase 2)")
