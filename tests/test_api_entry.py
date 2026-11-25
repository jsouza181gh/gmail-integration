from http.cookies import SimpleCookie

import pyotp
import pytest
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.api import google_entry
from src.api.google_entry import GMAIL_SCOPE_URLS, login_handler, signup_handler
from src.db.models import GmailAccount, Invite, SecurityEvent, ToolPolicy, User
from src.db.scope import account_scope, system_scope
from src.gmail import oauth
from src.gmail.oauth import GoogleIdentity, OAuthState
from tests.api_fakes import PASSWORD, SETTINGS, Api
from tests.auth_fakes import make_user

pytestmark = pytest.mark.usefixtures("_platform")
INVITE_EMAIL = "ana@example.com"


class Saved:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int]] = []
        self.fail = False


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> Saved:
    rec = Saved()

    def fake_save(sf: sessionmaker[Session], account_id: int, identity: GoogleIdentity, client: int) -> None:
        if rec.fail:
            raise RuntimeError("google caiu")
        rec.calls.append((account_id, identity.email, client))

    monkeypatch.setattr(google_entry, "save_connection", fake_save)
    return rec


@pytest.fixture
def _platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_entry, "platform_oauth_client_id", lambda sf: 7)
    monkeypatch.setenv("APP_ORIGIN", "https://panel.test")


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    return Api(session_factory)


def identity(email: str = INVITE_EMAIL, *, verified: bool = True, sub: str = "sub-ana",
             scopes: tuple[str, ...] = GMAIL_SCOPE_URLS, refresh: str | None = "rt") -> GoogleIdentity:
    return GoogleIdentity(email=email, email_verified=verified, sub=sub, refresh_token=refresh,
                          scopes=["openid", "email", *scopes])


def state(purpose: oauth.OAuthPurpose, token: str | None = None) -> OAuthState:
    return OAuthState(purpose=purpose, user_id=None, verifier="v", invite_token=token, exp=0)


def jar_value(api: Api, name: str) -> str | None:
    """The newest cookie called `name` (responses and `adopt` may both have stored one)."""
    values = [c.value for c in api.client.cookies.jar if c.name == name]
    return values[-1] if values else None


def adopt(api: Api, response: Response) -> None:
    """What the browser does with the callback's Set-Cookie headers."""
    for raw in response.headers.getlist("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(raw)
        for name, morsel in jar.items():
            api.client.cookies.delete(name)
            api.client.cookies.set(name, morsel.value, path="/")
    csrf = jar_value(api, "csrf")
    if csrf is not None:
        api.client.headers["X-CSRF-Token"] = csrf


def admin_session(api: Api) -> None:
    api.login()


def invite(api: Api, email: str = INVITE_EMAIL) -> str:
    admin_session(api)
    r = api.client.post("/api/admin/invites", json={"email": email})
    assert r.status_code == 200, r.text
    link: str = r.json()["link"]
    api.client.cookies.clear()
    api.client.headers.pop("X-CSRF-Token", None)
    return link.split("convite=")[1]


def where(response: Response) -> str:
    assert response.status_code == 303
    return response.headers["location"]


# --- admin invites ---------------------------------------------------------------

def test_admin_creates_lists_and_revokes_invites(api: Api) -> None:
    api.login()
    r = api.client.post("/api/admin/invites", json={"email": " Ana@Example.com "})
    body = r.json()
    assert r.status_code == 200 and body["link"].startswith("https://panel.test/entrar?convite=")
    assert body["invite"]["email"] == INVITE_EMAIL and body["invite"]["status"] == "open"
    listing = api.client.get("/api/admin/invites").json()
    assert [(i["email"], i["status"]) for i in listing] == [(INVITE_EMAIL, "open")]
    assert "link" not in listing[0] and "token" not in str(listing)
    assert api.client.post("/api/admin/invites", json={"email": "sem-arroba"}).status_code == 400
    assert api.client.delete(f"/api/admin/invites/{body['invite']['id']}").status_code == 204
    assert api.client.delete(f"/api/admin/invites/{body['invite']['id']}").status_code == 404
    assert api.client.get("/api/admin/invites").json()[0]["status"] == "revoked"
    with api.sf() as s:
        assert "invite_created" in list(s.scalars(select(SecurityEvent.kind)))


def test_members_and_anonymous_cannot_touch_invites(
    api: Api, session_factory: sessionmaker[Session]
) -> None:
    assert api.client.get("/api/admin/invites").status_code == 401
    make_user(session_factory, api.deps.settings, "bia", "uma-senha-bem-longa", email="bia@example.com",
              role="member")
    assert api.client.post("/api/auth/login", json={"username": "bia", "password": "uma-senha-bem-longa"}).status_code == 204
    # (member without 2FA completed has no session: login stops at the code step)
    assert api.client.get("/api/admin/invites").status_code == 401


def test_a_logged_in_member_gets_403(api: Api, session_factory: sessionmaker[Session]) -> None:
    enrollment = make_user(session_factory, api.deps.settings, "bia", "uma-senha-bem-longa",
                           email="bia@example.com", role="member")
    secret = pyotp.parse_uri(enrollment.otpauth_uri).secret
    api.client.post("/api/auth/login", json={"username": "bia", "password": "uma-senha-bem-longa"})
    assert api.client.post("/api/auth/2fa", json={"code": pyotp.TOTP(secret).at(api.clock())}).status_code == 204
    api.client.headers["X-CSRF-Token"] = jar_value(api, "csrf") or ""
    assert api.client.get("/api/admin/invites").status_code == 403
    assert api.client.post("/api/admin/invites", json={"email": "x@y.com"}).status_code == 403


# --- entry pages ---------------------------------------------------------------

def test_invite_lookup_and_google_urls(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str | None]] = []

    def fake_url(
        purpose: oauth.OAuthPurpose, *, user_id: int | None, invite_token: str | None, nonce: str
    ) -> str:
        assert len(nonce) >= 32  # every entry URL is bound to the browser
        seen.append((purpose, invite_token))
        return f"https://accounts.google.com/o/oauth2/v2/auth?p={purpose}"

    monkeypatch.setattr(oauth, "authorization_url", fake_url)
    token = invite(api)
    # no session, no CSRF cookie: only the Origin header is required
    r = api.client.post("/api/entry/invite", json={"token": token})
    assert (r.status_code, r.json()) == (200, {"email": INVITE_EMAIL})
    assert api.client.post("/api/entry/invite", json={"token": "lixo"}).status_code == 404
    r = api.client.post("/api/entry/signup", json={"token": token})
    assert r.json() == {"url": "https://accounts.google.com/o/oauth2/v2/auth?p=signup"}
    assert api.client.post("/api/entry/signup", json={"token": "lixo"}).status_code == 404
    assert api.client.post("/api/entry/login").json()["url"].endswith("p=login")
    assert seen == [("signup", token), ("login", None)]


def test_callbacks_are_registered_in_phase_2s_registry(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    got: dict[str, object] = {}
    monkeypatch.setattr(oauth, "register_callback", lambda purpose, handler: got.update({purpose: handler}))
    google_entry.register_entry_callbacks()
    assert got == {"signup": signup_handler, "login": login_handler}


# --- signup -------------------------------------------------------------------------

def test_signup_creates_user_and_account_then_onboarding_walks_to_done(
    api: Api, saved: Saved, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_scope(1), session_factory.begin() as s:
        s.add(ToolPolicy(target="apply_labels", trigger="webhook", policy="auto"))
    token = invite(api)
    resp = signup_handler(state("signup", token), identity(), api.deps)
    assert where(resp) == "/onboarding"
    with system_scope(), session_factory() as s:
        user = s.scalars(select(User).where(User.email == INVITE_EMAIL)).one()
        account = s.scalars(select(GmailAccount).where(GmailAccount.user_id == user.id)).one()
        assert (user.role, user.onboarding_step, user.google_sub) == ("member", "totp", "sub-ana")
        assert user.password_hash is None and user.totp_secret_enc is None
        assert s.scalars(select(Invite)).one().accepted_at is not None
        account_id = account.id
    assert saved.calls == [(account_id, INVITE_EMAIL, 7)]
    with account_scope(account_id), session_factory() as s:  # the generic config came along
        assert [p.target for p in s.scalars(select(ToolPolicy))] == ["apply_labels"]
    set_cookies = resp.headers.getlist("set-cookie")
    onb = next(c for c in set_cookies if c.startswith("onb="))
    assert "HttpOnly" in onb and "Secure" in onb and "SameSite=strict" in onb

    adopt(api, resp)
    c = api.client
    assert c.get("/api/auth/me").status_code == 401  # the onboarding cookie opens nothing else
    assert c.get("/api/admin/invites").status_code == 401
    st = c.get("/api/onboarding/state").json()
    assert (st["step"], st["done"], st["email"], st["totp_confirmed"]) == ("totp", False, INVITE_EMAIL, False)

    started = c.post("/api/onboarding/totp/start").json()
    qr = c.get("/api/onboarding/totp/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/svg+xml" and b"<svg" in qr.content
    assert started["uri"].startswith("otpauth://totp/") and INVITE_EMAIL.replace("@", "%40") in started["uri"]
    bad = c.post("/api/onboarding/totp/confirm", json={"code": "000000"})
    assert bad.status_code == 400 and "código" in bad.json()["detail"]
    assert c.post("/api/onboarding/recovery/ack").status_code == 409  # codes not shown yet
    codes = c.post(
        "/api/onboarding/totp/confirm", json={"code": pyotp.TOTP(started["secret"]).at(api.clock())}
    ).json()["codes"]
    assert len(codes) == 10
    assert c.get("/api/onboarding/state").json()["totp_confirmed"] is True

    ack = c.post("/api/onboarding/recovery/ack")
    assert ack.status_code == 204 and "access=" in "".join(ack.headers.get_list("set-cookie"))
    c.headers["X-CSRF-Token"] = jar_value(api, "csrf") or ""
    me = c.get("/api/auth/me").json()
    assert (me["onboarding_step"], me["onboarding_done"]) == ("password_optional", False)

    assert c.post("/api/onboarding/password", json={"password": "curta"}).status_code == 409
    r = c.post("/api/onboarding/password", json={"password": "uma-senha-bem-longa"})
    assert r.status_code == 204
    assert c.get("/api/auth/me").json()["onboarding_step"] == "llm_key"

    api.deps.key_validator = lambda key: None  # never Google from a test
    monkeypatch.setenv("APP_SECRET", SETTINGS.app_secret)  # the key is stored sealed
    r = c.post("/api/onboarding/llm-key", json={"app_key": "AIza-good"})
    assert r.status_code == 200 and r.json()["next_step"] == "finish"
    assert c.post("/api/onboarding/finish", json={"backfill": False}).status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["onboarding_done"] is True
    with session_factory() as s:
        assert "onboarding_done" in list(s.scalars(select(SecurityEvent.kind)))
        assert "signup" in list(s.scalars(select(SecurityEvent.kind)))


def test_signup_without_session_cannot_write_without_csrf(api: Api, saved: Saved) -> None:
    token = invite(api)
    resp = signup_handler(state("signup", token), identity(), api.deps)
    adopt(api, resp)
    api.client.headers.pop("X-CSRF-Token")
    # cookies adopted but the CSRF header is missing -> refused
    assert api.client.post("/api/onboarding/totp/start").status_code == 403


def test_password_step_can_be_skipped(api: Api, saved: Saved) -> None:
    token = invite(api)
    adopt(api, signup_handler(state("signup", token), identity(), api.deps))
    c = api.client
    started = c.post("/api/onboarding/totp/start")
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    c.post("/api/onboarding/totp/confirm", json={"code": pyotp.TOTP(secret).at(api.clock())})
    c.post("/api/onboarding/recovery/ack")
    c.headers["X-CSRF-Token"] = jar_value(api, "csrf") or ""
    assert c.post("/api/onboarding/password/skip").status_code == 204
    me = c.get("/api/auth/me").json()
    assert (me["onboarding_step"], me["onboarding_done"]) == ("llm_key", False)  # the Gemini key is next
    c.cookies.clear()
    # no password was set: the e-mail cannot be used for password login
    assert c.post("/api/auth/login", json={"username": INVITE_EMAIL, "password": PASSWORD}).status_code == 401


@pytest.mark.parametrize(
    ("ident", "use_token", "expected"),
    [
        (identity(verified=False), True, "email_nao_verificado"),
        (identity(email="outra@example.com"), True, "outro_email"),
        (identity(scopes=GMAIL_SCOPE_URLS[:1]), True, "permissao"),
        (identity(refresh=None), True, "permissao"),
    ],
)
def test_signup_problems_end_on_friendly_pages_and_create_nothing(
    api: Api, saved: Saved, session_factory: sessionmaker[Session],
    ident: GoogleIdentity, use_token: bool, expected: str,
) -> None:
    token = invite(api)
    resp = signup_handler(state("signup", token), ident, api.deps)
    assert where(resp) == f"/entrar?convite={token}&erro={expected}"
    assert "set-cookie" not in resp.headers
    with system_scope(), session_factory() as s:
        assert list(s.scalars(select(User.email).where(User.email == INVITE_EMAIL))) == []
        assert s.scalars(select(Invite)).one().accepted_at is None  # the link still works
    assert saved.calls == []


def test_signup_without_a_valid_invite_goes_to_not_invited(api: Api, saved: Saved) -> None:
    assert where(signup_handler(state("signup", None), identity(), api.deps)) == "/nao-convidado"
    token = invite(api)
    revoked = api.deps.sf
    with revoked.begin() as s:
        row = s.scalars(select(Invite)).one()
        row.revoked_at = api.clock()
    assert where(signup_handler(state("signup", token), identity(), api.deps)) == (
        f"/entrar?convite={token}&erro=outro_email"
    )


def test_signup_by_someone_who_already_has_an_account_points_to_login(
    api: Api, saved: Saved
) -> None:
    token = invite(api, "joao@example.com")  # the admin's own e-mail
    resp = signup_handler(state("signup", token), identity(email="joao@example.com", sub="s"), api.deps)
    assert where(resp) == "/login?erro=ja_tem_conta"


def test_signup_failure_after_creating_rows_is_undone(
    api: Api, saved: Saved, session_factory: sessionmaker[Session]
) -> None:
    token = invite(api)
    saved.fail = True
    resp = signup_handler(state("signup", token), identity(), api.deps)
    assert where(resp) == f"/entrar?convite={token}&erro=falha"
    with system_scope(), session_factory() as s:
        assert list(s.scalars(select(User.email).where(User.email == INVITE_EMAIL))) == []
        assert [a.email_address for a in s.scalars(select(GmailAccount))] == ["me@example.com"]
        assert s.scalars(select(Invite)).one().accepted_at is None
    saved.fail = False  # the friend retries with the same link and it works
    assert where(signup_handler(state("signup", token), identity(), api.deps)) == "/onboarding"


# --- login --------------------------------------------------------------------------

def test_google_login_binds_the_sub_by_email_then_asks_for_the_code(api: Api) -> None:
    resp = login_handler(state("login"), identity("joao@example.com", sub="sub-joao"), api.deps)
    assert where(resp) == "/login?etapa=codigo"
    with api.sf() as s:
        assert s.get_one(User, 1).google_sub == "sub-joao"
    adopt(api, resp)
    assert api.client.get("/api/auth/me").status_code == 401  # Google alone is not a session
    r = api.client.post("/api/auth/2fa", json={"code": api.code()})
    assert r.status_code == 204, r.text
    api.client.headers["X-CSRF-Token"] = jar_value(api, "csrf") or ""
    assert api.client.get("/api/auth/me").json()["username"] == "joao"
    # next time it matches by sub, even if the e-mail changed
    again = login_handler(state("login"), identity("novo@example.com", sub="sub-joao"), api.deps)
    assert where(again) == "/login?etapa=codigo"


def test_google_login_with_another_identity_for_a_bound_email_is_refused(api: Api) -> None:
    login_handler(state("login"), identity("joao@example.com", sub="sub-joao"), api.deps)
    other = login_handler(state("login"), identity("joao@example.com", sub="sub-impostor"), api.deps)
    assert where(other) == "/nao-convidado"


def test_google_login_unknown_email_is_not_invited(api: Api) -> None:
    resp = login_handler(state("login"), identity("ninguem@example.com", sub="x"), api.deps)
    assert where(resp) == "/nao-convidado" and "set-cookie" not in resp.headers
    assert where(login_handler(state("login"), identity("x@y.com", verified=False), api.deps)) == (
        "/login?erro=email_nao_verificado"
    )


def test_google_login_with_a_pending_invite_points_back_to_the_link(api: Api) -> None:
    invite(api)
    resp = login_handler(state("login"), identity(), api.deps)
    assert where(resp) == "/entrar?erro=use_o_convite"


def test_google_login_of_a_locked_user_is_closed(api: Api) -> None:
    for ip in ("2.2.2.2", "3.3.3.3", "4.4.4.4"):
        api.deps.auth.password_step("joao", "errada", ip)
    resp = login_handler(state("login"), identity("joao@example.com", sub="s"), api.deps)
    assert where(resp) == "/login?erro=bloqueado" and "set-cookie" not in resp.headers


def test_google_login_in_the_middle_of_onboarding_resumes_it(
    api: Api, session_factory: sessionmaker[Session]
) -> None:
    make_user(session_factory, api.deps.settings, "bia@example.com", None, email="bia@example.com",
              totp=False, onboarding_step="totp", google_sub="sub-bia", user_id=4)
    resp = login_handler(state("login"), identity("bia@example.com", sub="sub-bia"), api.deps)
    assert where(resp) == "/onboarding"
    assert any(c.startswith("onb=") for c in resp.headers.getlist("set-cookie"))


def test_google_login_after_a_2fa_reset_goes_back_to_enrollment(
    api: Api, session_factory: sessionmaker[Session]
) -> None:
    make_user(session_factory, api.deps.settings, "bia@example.com", None, email="bia@example.com",
              totp=False, onboarding_step=None, google_sub="sub-bia", user_id=4)
    resp = login_handler(state("login"), identity("bia@example.com", sub="sub-bia"), api.deps)
    assert where(resp) == "/onboarding"
    with session_factory() as s:
        assert s.get_one(User, 4).onboarding_step == "totp"


def test_onboarding_is_closed_to_finished_users(api: Api) -> None:
    api.login()
    assert api.client.post("/api/onboarding/totp/start").status_code == 409
    assert api.client.get("/api/onboarding/state").json()["done"] is True
