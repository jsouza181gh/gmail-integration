"""/api/entry: where a visitor without a session starts (invite page, Google buttons)."""
from fastapi import APIRouter, HTTPException, Request, Response

from src.api.deps import get_deps
from src.auth.invites import invite_for_token
from src.gmail import oauth
from src.llm.types import Schema

router = APIRouter(prefix="/api/entry", tags=["entry"])

INVALID_INVITE = "Esse convite não vale mais. Peça um novo link a quem convidou você."


class TokenIn(Schema):
    token: str


class InviteInfoOut(Schema):
    email: str


class UrlOut(Schema):
    url: str


@router.post("/invite")
def invite_info(body: TokenIn, request: Request) -> InviteInfoOut:
    invite = invite_for_token(get_deps(request).sf, body.token[:200])
    if invite is None:
        raise HTTPException(404, INVALID_INVITE)
    return InviteInfoOut(email=invite.email)


@router.post("/signup")
def signup(body: TokenIn, request: Request, response: Response) -> UrlOut:
    token = body.token[:200]
    if invite_for_token(get_deps(request).sf, token) is None:
        raise HTTPException(404, INVALID_INVITE)
    return UrlOut(url=oauth.start_flow(response, "signup", user_id=None, invite_token=token))


@router.post("/login")
def login(response: Response) -> UrlOut:
    return UrlOut(url=oauth.start_flow(response, "login", user_id=None, invite_token=None))
