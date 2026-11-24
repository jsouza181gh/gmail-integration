"""/api/backfill, /api/runs, /api/jobs, /api/usage: what the system is doing."""
import threading
import weakref
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select, update

from src.agent.settings import RuntimeSettings
from src.agent.trail import Trail
from src.api import queries
from src.api.deps import ApiDeps, get_deps, require_user
from src.db.models import AgentRun, Job, LlmCall
from src.db.rows import AgentStepRow
from src.db.scope import current_account
from src.indexing.backfill import PAGE_SIZE, PAUSED_REASON, RUN_QUERY, BackfillProgress
from src.jobs.types import BackfillPageJob, DeadJob, JobCounts
from src.llm.errors import NoLLMKeyError
from src.llm.types import Schema
from src.utils.console import log

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)], tags=["ops"])
PROFILE_TTL = timedelta(minutes=10)


class BackfillStatus(Schema):
    progress: BackfillProgress
    mailbox_total: int | None
    percent: float | None
    pages_last_hour: int
    pages_last_day: int
    eta_hours: float | None
    deferred_until: datetime | None
    paused: bool  # paused by the user (deferred_until is the quota wait)
    turbo: bool  # the backfill also uses the app key while it is idle
    turbo_available: bool  # a separate GOOGLE_API_KEY_BACKFILL is set


class TurboIn(Schema):
    on: bool


class LlmCallOut(Schema):
    id: int
    created_at: datetime
    purpose: str
    model: str
    attempt: int
    status: str
    error_kind: str | None
    error: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class RunDetail(Schema):
    run: queries.RunOut
    steps: list[AgentStepRow]
    llm_calls: list[LlmCallOut]


class JobsOut(Schema):
    counts: dict[str, JobCounts]
    dead: list[DeadJob]


class UsageDay(Schema):
    day: date
    purpose: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int


class _Profile(BaseModel):
    messagesTotal: int


class _ProfileCache:
    """Mailbox size from Gmail, cached per account: it only feeds a percentage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[int, tuple[datetime, int | None]] = {}

    def total(self, deps: ApiDeps) -> int | None:
        now = deps.clock()
        account = current_account()
        with self._lock:
            hit = self._entries.get(account)
            if hit is not None and now - hit[0] < PROFILE_TTL:
                return hit[1]
        try:
            raw = deps.gmail().users().getProfile(userId="me").execute(num_retries=2)
            value: int | None = _Profile.model_validate(raw).messagesTotal
        except Exception as exc:  # noqa: BLE001 - a missing total only hides the percentage
            log(f"getProfile falhou: {type(exc).__name__}: {exc}")
            value = None
        with self._lock:
            self._entries[account] = (now, value)
        return value


# one cache per running app (its deps), keyed inside by account
_profiles: "weakref.WeakKeyDictionary[ApiDeps, _ProfileCache]" = weakref.WeakKeyDictionary()


def _profile_total(deps: ApiDeps) -> int | None:
    cache = _profiles.get(deps)
    if cache is None:
        cache = _profiles.setdefault(deps, _ProfileCache())
    return cache.total(deps)


def _progress(deps: ApiDeps) -> BackfillProgress:
    run = deps.index_store.backfill_run(RUN_QUERY)
    counts = deps.index_store.run_counts(run.id)
    return BackfillProgress(
        run_id=run.id, pages=run.pages, fetch_done=run.fetch_done,
        **counts.model_dump(), jobs=deps.jobs.counts_by_kind(),
    )


def _status(deps: ApiDeps) -> BackfillStatus:
    p = _progress(deps)
    now = deps.clock()
    with deps.sf() as s:
        def finished_since(delta: timedelta) -> int:
            return s.scalar(
                select(func.count()).select_from(Job).where(
                    Job.kind == "backfill_page", Job.status == "done", Job.run_id == p.run_id,
                    Job.finished_at > now - delta,
                )
            ) or 0

        hour, day = finished_since(timedelta(hours=1)), finished_since(timedelta(days=1))
        first_today = s.scalar(
            select(func.min(Job.finished_at)).where(
                Job.kind == "backfill_page", Job.status == "done", Job.run_id == p.run_id,
                Job.finished_at > now - timedelta(days=1),
            )
        )
        next_at = s.scalar(
            select(func.min(Job.run_after)).where(
                Job.kind == "backfill_page", Job.status == "queued", Job.run_id == p.run_id,
                or_(Job.last_error.is_(None), Job.last_error != PAUSED_REASON),
            )
        )
    total = _profile_total(deps)
    percent = None
    eta = None
    if total:
        percent = round(min(100.0, 100.0 * p.emails / total), 1)
        remaining_pages = max(0, total - p.emails) / PAGE_SIZE
        # pages per hour over the stretch actually worked today (>= 1 h)
        if first_today is not None and first_today.tzinfo is None:
            first_today = first_today.replace(tzinfo=now.tzinfo)
        span_h = max(1.0, (now - first_today).total_seconds() / 3600) if first_today else 24.0
        rate = day / span_h if day else float(hour)
        eta = round(remaining_pages / rate, 1) if rate and not p.fetch_done else None
    if next_at is not None and next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=now.tzinfo)
    return BackfillStatus(
        progress=p, mailbox_total=total, percent=100.0 if p.fetch_done else percent,
        pages_last_hour=hour, pages_last_day=day, eta_hours=eta,
        deferred_until=next_at if next_at is not None and next_at > now else None,
        paused=RuntimeSettings(deps.sf).backfill_paused(),
        turbo=RuntimeSettings(deps.sf).backfill_turbo(),
        turbo_available=_has_separate_key(deps),
    )


@router.get("/backfill")
def backfill(request: Request) -> BackfillStatus:
    return _status(get_deps(request))


def _has_separate_key(deps: ApiDeps) -> bool:
    try:
        return deps.llm_keys.keys(current_account()).separate
    except NoLLMKeyError:
        return False


def _start(deps: ApiDeps) -> None:
    try:
        deps.llm_keys.keys(current_account())
    except NoLLMKeyError as exc:  # the history needs the user's own Gemini key
        raise HTTPException(409, str(exc)) from exc
    run = deps.index_store.backfill_run(RUN_QUERY)
    if not run.fetch_done:
        deps.jobs.enqueue(BackfillPageJob(run_id=run.id, page_no=run.pages))


@router.post("/backfill/start")
def backfill_start(request: Request) -> BackfillStatus:
    deps = get_deps(request)
    _start(deps)
    return _status(deps)


@router.post("/backfill/pause")
def backfill_pause(request: Request) -> BackfillStatus:
    deps = get_deps(request)
    RuntimeSettings(deps.sf, deps.clock).set_backfill_paused(True)
    return _status(deps)


@router.post("/backfill/turbo")
def backfill_turbo(body: TurboIn, request: Request) -> BackfillStatus:
    """Turbo: the backfill also uses the app key, but never while the app needs it."""
    deps = get_deps(request)
    RuntimeSettings(deps.sf, deps.clock).set_backfill_turbo(body.on)
    return _status(deps)


@router.post("/backfill/resume")
def backfill_resume(request: Request) -> BackfillStatus:
    """Unpause; the page the worker parked for the pause runs now, not in 5 min."""
    deps = get_deps(request)
    RuntimeSettings(deps.sf, deps.clock).set_backfill_paused(False)
    with deps.sf.begin() as s:
        s.execute(
            update(Job)
            .where(Job.kind == "backfill_page", Job.status == "queued",
                   Job.last_error == PAUSED_REASON)
            .values(run_after=deps.clock(), last_error=None)
        )
    _start(deps)
    return _status(deps)


@router.get("/runs")
def runs(
    request: Request,
    trigger: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[queries.RunOut]:
    stmt = select(AgentRun)
    if trigger:
        stmt = stmt.where(AgentRun.trigger == trigger)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    with get_deps(request).sf() as s:
        return [queries.run_out(r) for r in s.scalars(stmt.order_by(AgentRun.id.desc()).limit(limit))]


@router.get("/runs/{run_id}")
def run_detail(run_id: int, request: Request) -> RunDetail:
    deps = get_deps(request)
    with deps.sf() as s:
        run = s.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(404, "execução não encontrada")
        calls = s.scalars(select(LlmCall).where(LlmCall.agent_run_id == run_id).order_by(LlmCall.id))
        call_out = [
            LlmCallOut(
                id=c.id, created_at=c.created_at, purpose=c.purpose, model=c.model, attempt=c.attempt,
                status=c.status, error_kind=c.error_kind, error=c.error, latency_ms=c.latency_ms,
                input_tokens=c.input_tokens, output_tokens=c.output_tokens, total_tokens=c.total_tokens,
            )
            for c in calls
        ]
        out = queries.run_out(run)
    return RunDetail(run=out, steps=list(Trail(deps.sf).steps(run_id)), llm_calls=call_out)


@router.get("/jobs")
def jobs(request: Request, limit: int = Query(50, ge=1, le=500)) -> JobsOut:
    store = get_deps(request).jobs
    return JobsOut(counts=store.counts_by_kind(), dead=store.dead(limit))


@router.post("/jobs/{job_id}/requeue", status_code=204)
def requeue(job_id: int, request: Request) -> None:
    jobs = get_deps(request).jobs
    if not jobs.exists(job_id):
        raise HTTPException(404, "job não encontrado")
    if not jobs.requeue(job_id):
        raise HTTPException(409, "o job não está morto")


@router.get("/usage")
def usage(request: Request, days: int = Query(7, ge=1, le=90)) -> list[UsageDay]:
    deps = get_deps(request)
    since = deps.clock() - timedelta(days=days)
    day = func.date(LlmCall.created_at)
    with deps.sf() as s:
        rows = s.execute(
            select(
                day, LlmCall.purpose, func.count(),
                func.sum(case((LlmCall.status == "error", 1), else_=0)),
                func.sum(LlmCall.input_tokens), func.sum(LlmCall.output_tokens),
            )
            .where(LlmCall.created_at >= since)
            .group_by(day, LlmCall.purpose)
            .order_by(day.desc(), LlmCall.purpose)
        ).all()
    return [
        UsageDay(
            day=d if isinstance(d, date) else date.fromisoformat(str(d)), purpose=p, calls=n,
            errors=int(e or 0), input_tokens=int(i or 0), output_tokens=int(o or 0),
        )
        for d, p, n, e, i, o in rows
    ]
