"""What the API routes need, built once per process, and the auth dependency."""
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from googleapiclient.discovery import Resource
from sqlalchemy.orm import Session, sessionmaker

from src.agent.actions import ActionService
from src.agent.approvals import ApprovalService
from src.agent.kb import KbAdmin
from src.agent.policy import PolicyAdmin, PolicyResolver
from src.agent.rules import RuleAdmin, RuleEngine
from src.auth.config import AuthSettings
from src.auth.crypto import read_access
from src.auth.security_log import Alerter, SecurityLog
from src.auth.service import AuthService
from src.chat.cards import CardService
from src.chat.store import ChatStore
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.llm.accounts import KeyResolver
from src.llm.config import env_account_keys
from src.llm.keys import validate_key

ACCESS_COOKIE = "access"
REFRESH_COOKIE = "refresh"
MFA_COOKIE = "mfa"
CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _no_gmail() -> Resource:
    raise RuntimeError("Gmail indisponível nesta instância da API")


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
        log: SecurityLog,
        jobs: JobStore,
        approvals: ApprovalService,
        index_store: IndexStore,
        gmail: Callable[[], Resource],
        clock: Callable[[], datetime],
        chat: ChatStore,
        cards: CardService,
        stream: "StreamTiming",
        llm_keys: KeyResolver,
        key_validator: Callable[[str], str | None],
    ) -> None:
        self.sf = sf
        self.settings = settings
        self.auth = auth
        self.log = log
        self.jobs = jobs
        self.approvals = approvals
        self.index_store = index_store
        self.gmail = gmail
        self.clock = clock
        self.chat = chat
        self.cards = cards
        self.stream = stream
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
            log=log,
            jobs=jobs,
            approvals=approvals,
            index_store=IndexStore(sf),
            gmail=gmail,
            clock=clock,
            chat=chat,
            cards=cards,
            stream=stream or StreamTiming(),
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


def request_ip(request: Request) -> str:
    ip = getattr(request.state, "ip", None)
    return ip if isinstance(ip, str) else (request.client.host if request.client else "unknown")
