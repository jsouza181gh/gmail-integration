"""What the API routes need, built once per process, and the auth dependency."""
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from googleapiclient.discovery import Resource
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.kb import KbAdmin
from src.agent.policy import PolicyAdmin, PolicyResolver
from src.agent.rules import RuleAdmin, RuleEngine
from src.auth.config import AuthSettings
from src.auth.crypto import read_access, read_onboarding
from src.auth.onboarding import Onboarding
from src.auth.security_log import Alerter, SecurityLog
from src.auth.service import AuthService
from src.chat.cards import CardService
from src.chat.store import ChatStore
from src.db.models import User
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.llm.accounts import KeyResolver
from src.llm.config import env_account_keys
from src.llm.keys import validate_key

ACCESS_COOKIE = "access"
REFRESH_COOKIE = "refresh"
MFA_COOKIE = "mfa"
ONB_COOKIE = "onb"
CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _no_gmail() -> Resource:
    raise RuntimeError("Gmail indisponível nesta instância da API")


def _nothing_to_start(account_id: int) -> None:
    """Default `on_connected`: nothing watches the mailbox in this instance."""


class StreamTiming:
    """How the chat SSE stream polls the database (seconds)."""

    def __init__(self, poll_s: float = 0.75, max_s: float = 180.0, ping_s: float = 15.0) -> None:
        self.poll_s = poll_s
        self.max_s = max_s
        self.ping_s = ping_s


class ApiDeps:
    def __init__(
        self,
        *,
        sf: sessionmaker[Session],
        settings: AuthSettings,
        auth: AuthService,
        onboarding: Onboarding,
        log: SecurityLog,
        jobs: JobStore,
        approvals: ApprovalService,
        index_store: IndexStore,
        gmail: Callable[[], Resource],
        clock: Callable[[], datetime],
        chat: ChatStore,
        cards: CardService,
        stream: "StreamTiming",
        on_connected: Callable[[int], None] = _nothing_to_start,
        llm_keys: KeyResolver,
        key_validator: Callable[[str], str | None],
    ) -> None:
        self.sf = sf
        self.settings = settings
        self.auth = auth
        self.onboarding = onboarding
        self.log = log
        self.jobs = jobs
        self.approvals = approvals
        self.index_store = index_store
        self.gmail = gmail
        self.clock = clock
        self.chat = chat
        self.cards = cards
        self.stream = stream
        self.on_connected = on_connected  # account id: its Gmail was (re)connected
        self.llm_keys = llm_keys  # the account's Gemini keys (cached)
        self.key_validator = key_validator  # None = key works; text = what to tell the user

    @classmethod
    def build(
        cls,
        sf: sessionmaker[Session],
        settings: AuthSettings,
        *,
        alerts: Alerter,
        gmail: Callable[[], Resource] = _no_gmail,
        clock: Callable[[], datetime] = _utcnow,
        stream: "StreamTiming | None" = None,
        on_connected: Callable[[int], None] = _nothing_to_start,
        key_validator: Callable[[str], str | None] = validate_key,
    ) -> "ApiDeps":
        log = SecurityLog(sf, alerts, clock)
        jobs = JobStore(sf)
        approvals = ApprovalService(sf, jobs, clock)
        chat = ChatStore(sf, clock)
        cards = CardService(
            store=chat,
            rules=RuleAdmin(sf, clock),
            matcher=RuleEngine(sf),
            kb=KbAdmin(sf, clock),
            policies=PolicyAdmin(sf, clock),
            approvals=approvals,
            actions=ActionService(sf, jobs, PolicyResolver(sf), clock),
            clock=clock,
        )
        return cls(
            sf=sf,
            settings=settings,
            auth=AuthService(sf, settings, log, clock),
            onboarding=Onboarding(sf, settings, clock),
            log=log,
            jobs=jobs,
            approvals=approvals,
            index_store=IndexStore(sf),
            gmail=gmail,
            clock=clock,
            chat=chat,
            cards=cards,
            stream=stream or StreamTiming(),
            on_connected=on_connected,
            llm_keys=KeyResolver(sf, env_keys=env_account_keys),
            key_validator=key_validator,
        )


def get_deps(request: Request) -> ApiDeps:
    deps = getattr(request.app.state, "deps", None)
    if not isinstance(deps, ApiDeps):
        raise HTTPException(503, "API não configurada")
    return deps


def require_user(request: Request) -> int:
    deps = get_deps(request)
    token = request.cookies.get(ACCESS_COOKIE)
    user_id = read_access(token, deps.settings, deps.clock()) if token else None
    if user_id is None:
        raise HTTPException(401, "sessão expirada")
    return user_id


def optional_user(request: Request) -> int | None:
    """The logged-in user, if any (for events recorded outside `require_user` routes)."""
    deps = get_deps(request)
    token = request.cookies.get(ACCESS_COOKIE)
    return read_access(token, deps.settings, deps.clock()) if token else None


def require_admin(request: Request, user_id: int = Depends(require_user)) -> int:
    with get_deps(request).sf() as s:
        user = s.get(User, user_id)
        if user is None or user.role != "admin":
            raise HTTPException(403, "só o administrador")
    return user_id


def onboarding_user(request: Request) -> int:
    """The user of an onboarding call: a full session, or the Google-proof
    cookie that only opens /api/onboarding."""
    deps = get_deps(request)
    now = deps.clock()
    token = request.cookies.get(ACCESS_COOKIE)
    user_id = read_access(token, deps.settings, now) if token else None
    if user_id is None:
        onb = request.cookies.get(ONB_COOKIE)
        user_id = read_onboarding(onb, deps.settings, now) if onb else None
    if user_id is None:
        raise HTTPException(401, "sessão expirada")
    return user_id


def request_ip(request: Request) -> str:
    ip = getattr(request.state, "ip", None)
    return ip if isinstance(ip, str) else (request.client.host if request.client else "unknown")
