"""FastAPI app: receives Gmail Pub/Sub push notifications.

Run with: uvicorn src.api.app:build_app --factory --workers 1
(single worker: the sync lock and the renewal scheduler live in-process).
"""
import asyncio
import base64
import binascii
import hmac
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.deps import ApiDeps
from src.api.log_filters import install_access_log_filter
from src.api.pubsub_auth import PubSubVerifier
from src.api.security import RateLimiter, security_middleware
from src.utils.console import log


SYNC_FAILURE_STREAK = 3


class PushNotification(BaseModel):
    """Gmail payload carried in a Pub/Sub push message."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    emailAddress: str
    historyId: str


class _PushMessage(BaseModel):
    data: str


class _PushBody(BaseModel):
    message: _PushMessage


def decode_push(body: bytes) -> PushNotification | None:
    """Decode a raw Pub/Sub push body, or None when malformed."""
    try:
        envelope = _PushBody.model_validate_json(body)
        data = base64.b64decode(envelope.message.data, validate=True)
        return PushNotification.model_validate_json(data)
    except (ValueError, binascii.Error):  # pydantic ValidationError is a ValueError
        return None


async def _renew_loop(renew: Callable[[], None], interval_s: float, retry_s: float) -> None:
    delay = interval_s
    while True:
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(renew)
            delay = interval_s
        except Exception as exc:  # noqa: BLE001
            log(f"renovação do watch falhou: {type(exc).__name__}: {exc}")
            delay = retry_s


def create_app(
    *,
    trigger: Callable[[], None],
    token: str | None,
    on_startup: Callable[[], None] | None = None,
    renew: Callable[[], None] | None = None,
    renew_interval_s: float = 86400,
    retry_s: float = 3600,
    api: ApiDeps | None = None,
    pubsub: PubSubVerifier | None = None,
) -> FastAPI:
    """`api` enables the authenticated panel API under /api; without it only
    the webhook and /healthz exist (e.g. before the auth secrets are set)."""
    install_access_log_filter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if on_startup:
            try:
                await asyncio.to_thread(on_startup)
            except Exception as exc:  # noqa: BLE001
                log(f"startup falhou (API segue de pé): {type(exc).__name__}: {exc}")
        task = (
            asyncio.create_task(_renew_loop(renew, renew_interval_s, retry_s))
            if renew
            else None
        )
        try:
            yield
        finally:
            if task:
                task.cancel()

    hide_docs = api is not None and api.settings.production
    app = FastAPI(
        lifespan=lifespan,
        docs_url=None if hide_docs else "/docs",
        redoc_url=None if hide_docs else "/redoc",
        openapi_url=None if hide_docs else "/openapi.json",
    )
    app.middleware("http")(security_middleware(RateLimiter()))
    if api is not None:
        from src.api.routes import actions, auth, mail

        app.state.deps = api
        for module in (auth, mail, actions):
            app.include_router(module.router)

    @app.post("/webhooks/gmail")
    async def gmail_webhook(request: Request, background: BackgroundTasks) -> Response:
        if pubsub is not None:  # OIDC replaces the query token once configured
            if not pubsub.ok(request.headers.get("authorization")):
                return Response(status_code=401)
        elif token is not None:
            given = request.query_params.get("token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return Response(status_code=401)
        if decode_push(await request.body()) is None:
            log("push malformado; descartado")
        else:
            background.add_task(trigger)
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def build_app() -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv()

    from src.db.store import Store
    from src.gmail.client import get_gmail_service
    from src.gmail.watch import ensure_watch
    from src.jobs.store import JobStore
    from src.services.sync import SyncCoordinator, sync_inbox

    from src.alerts.service import FailureStreak, build_alert_service

    store = Store()
    # The API only ingests and enqueues; the worker process runs the agent.
    jobs = JobStore(store.sf)
    alerts = build_alert_service(store)
    syncs = FailureStreak(
        SYNC_FAILURE_STREAK,
        lambda n, err: alerts.alert("sync_failures", f"sync falhou {n} vezes seguidas", err),
    )

    def run_sync() -> None:
        # googleapiclient services are not thread-safe: one per sync run.
        try:
            sync_inbox(store, get_gmail_service(), jobs)
        except Exception as exc:
            syncs.failure(f"{type(exc).__name__}: {exc}")
            raise
        syncs.success()

    coordinator = SyncCoordinator(run_sync)

    def start_watch() -> None:
        ensure_watch(store, get_gmail_service())

    def renew_and_sync() -> None:
        try:
            start_watch()
        except Exception as exc:
            alerts.alert("watch_failures", "renovação do watch do Gmail falhou", f"{exc}")
            raise
        coordinator.trigger()

    api: ApiDeps | None = None
    if os.getenv("JWT_SECRET"):
        from src.auth.config import load_auth_settings

        api = ApiDeps.build(store.sf, load_auth_settings(), alerts=alerts, gmail=get_gmail_service)
    else:
        log("JWT_SECRET ausente: API do painel desligada (só webhook e /healthz)")

    audience, account = os.getenv("PUBSUB_AUDIENCE"), os.getenv("PUBSUB_SERVICE_ACCOUNT")
    pubsub = PubSubVerifier(audience, account) if audience and account else None

    return create_app(
        trigger=coordinator.trigger,
        token=os.getenv("WEBHOOK_TOKEN") or None,
        pubsub=pubsub,
        on_startup=start_watch,
        renew=renew_and_sync,
        api=api,
    )
