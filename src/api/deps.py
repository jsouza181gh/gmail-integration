"""What the API routes need, built once per process, and the auth dependency."""
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from googleapiclient.discovery import Resource
from sqlalchemy.orm import Session, sessionmaker

from src.agent.approvals import ApprovalService
from src.auth.config import AuthSettings
from src.auth.crypto import read_access
from src.auth.security_log import Alerter, SecurityLog
from src.auth.service import AuthService
from src.indexing.store import IndexStore
from src.jobs.store import JobStore

ACCESS_COOKIE = "access"
REFRESH_COOKIE = "refresh"
MFA_COOKIE = "mfa"
CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _no_gmail() -> Resource:
    raise RuntimeError("Gmail indisponível nesta instância da API")


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

    @classmethod
    def build(
        cls,
        sf: sessionmaker[Session],
        settings: AuthSettings,
        *,
        alerts: Alerter,
        gmail: Callable[[], Resource] = _no_gmail,
        clock: Callable[[], datetime] = _utcnow,
    ) -> "ApiDeps":
        log = SecurityLog(sf, alerts, clock)
        jobs = JobStore(sf)
        return cls(
            sf=sf,
            settings=settings,
            auth=AuthService(sf, settings, log, clock),
            log=log,
            jobs=jobs,
            approvals=ApprovalService(sf, jobs, clock),
            index_store=IndexStore(sf),
            gmail=gmail,
            clock=clock,
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
