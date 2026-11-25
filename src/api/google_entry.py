"""Google callbacks for `signup` (first access, by invite) and `login`.

Phase 2's OAuth module exchanges the code and hands us the verified
identity; here we decide what it means. Every outcome is a redirect to a
page of the panel (cookies set on it): errors become friendly screens, never
a JSON blob in the middle of the browser.
"""
from datetime import datetime

from fastapi import Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.api.deps import ApiDeps
from src.api.session_cookies import set_mfa, set_onboarding
from src.auth.invites import InviteRow, list_invites, open_invite_for
from src.auth.onboarding import FIRST_STEP_AFTER_SIGNUP, needs_enrollment
from src.db.models import GmailAccount, Invite, User
from src.db.scope import system_scope
from src.db.seed_account import generic_source_account, seed_generic_config
from src.gmail import oauth
from src.gmail.accounts_store import save_connection
from src.gmail.oauth import GoogleIdentity, OAuthState
from src.gmail.oauth_clients import platform_client
from src.utils.console import log

GMAIL_SCOPE_URLS = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
)


def platform_oauth_client_id(sf: sessionmaker[Session]) -> int:
    """The `oauth_clients` row (kind 'platform') the web consent belongs to."""
    client = platform_client(sf)
    if client is None:
        raise RuntimeError("não há client OAuth 'platform' (rode: python -m src.cli.oauth_client sync)")
    return client.id


def _go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _invite_page(token: str | None, error: str) -> RedirectResponse:
    return _go(f"/entrar?convite={token}&erro={error}" if token else f"/entrar?erro={error}")


def _existing_user(s: Session, identity: GoogleIdentity) -> User | None:
    return s.scalars(
        select(User).where((User.google_sub == identity.sub) | (User.email == identity.email))
    ).first()


def _granted_gmail(identity: GoogleIdentity) -> bool:
    return identity.refresh_token is not None and all(
        scope in identity.scopes for scope in GMAIL_SCOPE_URLS
    )


def _create_user_and_account(
    sf: sessionmaker[Session], identity: GoogleIdentity, invite: InviteRow, now: datetime
) -> tuple[int, int]:
    with system_scope(), sf.begin() as s:
        user = User(
            username=identity.email, email=identity.email, google_sub=identity.sub, role="member",
            onboarding_step=FIRST_STEP_AFTER_SIGNUP, created_at=now, password_changed_at=now,
        )
        s.add(user)
        s.flush()
        account = GmailAccount(user_id=user.id, email_address=identity.email,
                               status="disconnected", connected_at=now)
        s.add(account)
        s.flush()
        stored = s.get(Invite, invite.id)
        if stored is not None:
            stored.accepted_at = now
        return user.id, account.id


def _undo_signup(sf: sessionmaker[Session], user_id: int, account_id: int, invite_id: int) -> None:
    with system_scope(), sf.begin() as s:
        account = s.get(GmailAccount, account_id)
        if account is not None:
            s.delete(account)
        s.flush()
        user = s.get(User, user_id)
        if user is not None:
            s.delete(user)
        stored = s.get(Invite, invite_id)
        if stored is not None:
            stored.accepted_at = None


def signup_handler(state: OAuthState, identity: GoogleIdentity, deps: ApiDeps) -> Response:
    token = state.invite_token
    if not identity.email_verified:
        return _invite_page(token, "email_nao_verificado")
    with deps.sf() as s:
        if _existing_user(s, identity) is not None:
            return _go("/login?erro=ja_tem_conta")
    invite = open_invite_for(deps.sf, identity.email, token)  # the single entry check
    if invite is None:
        # a valid link opened with another Google account is a mistake worth explaining
        return _invite_page(token, "outro_email") if token else _go("/nao-convidado")
    if not _granted_gmail(identity):
        return _invite_page(token, "permissao")
    now = deps.clock()
    try:
        user_id, account_id = _create_user_and_account(deps.sf, identity, invite, now)
    except IntegrityError:
        return _invite_page(token, "falha")
    try:
        save_connection(deps.sf, account_id, identity, platform_oauth_client_id(deps.sf))
        source = generic_source_account(deps.sf)
        if source is not None and source != account_id:
            seed_generic_config(deps.sf, source=source, target=account_id)
    except Exception as exc:  # noqa: BLE001 - leave nothing half-created, let the friend retry
        log(f"signup de {identity.email} falhou: {type(exc).__name__}: {exc}")
        _undo_signup(deps.sf, user_id, account_id, invite.id)
        return _invite_page(token, "falha")
    deps.log.record("signup", None, f"conta criada para {identity.email}")
    response = _go("/onboarding")
    set_onboarding(response, deps, user_id)
    return response


def login_handler(state: OAuthState, identity: GoogleIdentity, deps: ApiDeps) -> Response:
    if not identity.email_verified:
        return _go("/login?erro=email_nao_verificado")
    with deps.sf.begin() as s:
        user = s.scalars(select(User).where(User.google_sub == identity.sub)).first()
        if user is None:
            user = s.scalars(select(User).where(User.email == identity.email)).first()
            if user is not None and user.google_sub is None:
                user.google_sub = identity.sub  # first Google login: bound by e-mail
            elif user is not None:
                user = None  # same e-mail, another Google identity: not the same person
        user_id = user.id if user is not None else None
        step = user.onboarding_step if user is not None else None
    if user_id is None:
        pending = any(
            i.email == identity.email and i.status == "open" for i in list_invites(deps.sf)
        )
        return _go("/entrar?erro=use_o_convite") if pending else _go("/nao-convidado")
    if not deps.auth.google_step(user_id).ok:
        return _go("/login?erro=bloqueado")
    if needs_enrollment(step) or deps.onboarding.begin_enrollment_if_needed(user_id):
        response = _go("/onboarding")
        set_onboarding(response, deps, user_id)
        return response
    response = _go("/login?etapa=codigo")
    set_mfa(response, deps, user_id)
    return response


def register_entry_callbacks() -> None:
    """Hook `signup` and `login` into phase 2's callback registry."""
    oauth.register_callback("signup", signup_handler)
    oauth.register_callback("login", login_handler)
