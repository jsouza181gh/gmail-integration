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
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from src.api.deps import ApiDeps
from src.api.log_filters import install_access_log_filter
from src.api.pubsub_auth import PubSubVerifier
from src.api.scope import AccountScopeMiddleware, ResolveAccount
from src.api.security import RateLimiter, security_middleware
from src.api.static import mount_web
from src.auth.crypto import read_access
from src.db.accounts import account_for_user
from src.services.sync import SyncCoordinators
from src.db.scope import ScopeError
from src.utils.console import log


SYNC_FAILURE_STREAK = 3
RENEW_WITHIN_MS = 2 * 86_400_000  # renew watches expiring in less than 2 days


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


def account_resolver(api: ApiDeps) -> ResolveAccount:
    """Access cookie -> the user's Gmail account (None: not logged in)."""

    def resolve(token: str) -> int | None:
        user_id = read_access(token, api.settings, api.clock())
        return account_for_user(api.sf, user_id) if user_id is not None else None

    return resolve


def create_app(
    *,
    trigger: Callable[[], None] | None = None,
    token: str | None,
    syncs: SyncCoordinators | None = None,
    resolve_account: Callable[[str], int | None] | None = None,
    on_startup: Callable[[], None] | None = None,
    renew: Callable[[], None] | None = None,
    renew_interval_s: float = 86400,
    retry_s: float = 3600,
    api: ApiDeps | None = None,
    pubsub: PubSubVerifier | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    """A push is routed by its `emailAddress`: `resolve_account` maps it to the
    connected account and `syncs` runs that account's sync only. Without them
    (tests, minimal instances) `trigger` runs on every push.

    `api` enables the authenticated panel API under /api; without it only
    the webhook and /healthz exist (e.g. before the auth secrets are set).
    `web_dist` (the built panel) is served at / only together with `api`."""
    install_access_log_filter()

    def route_push(email: str) -> None:
        account = resolve_account(email) if resolve_account else None
        if account is None or syncs is None:
            log("push de conta desconhecida ou desconectada; ignorado")
            return
        syncs.trigger(account)

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
        from src.api.google_entry import register_entry_callbacks
        from src.api.routes import (
            account,
            actions,
            admin,
            admin_invites,
            agent,
            auth,
            chat,
            documents,
            entry,
            gmail,
            mail,
            oauth,
            onboarding,
            ops,
            security,
            settings,
        )
        from src.gmail.oauth import configure as configure_oauth

        configure_oauth(api.sf, api.settings.origin)
        register_entry_callbacks()
        app.state.deps = api
        app.add_middleware(AccountScopeMiddleware, resolve=account_resolver(api))
        for module in (auth, entry, onboarding, admin_invites, admin, settings, account, mail,
                       actions, documents, ops, security, agent, chat, gmail, oauth):
            app.include_router(module.router)
        from src.api.routes import llm_key

        app.include_router(llm_key.router)

        @app.exception_handler(ScopeError)
        def no_account(request: Request, exc: ScopeError) -> JSONResponse:
            # a logged-in user with no Gmail account yet: send them to onboarding
            return JSONResponse(
                {"detail": "Conecte seu Gmail para continuar.", "code": "no_account"},
                status_code=409,
            )

    @app.post("/webhooks/gmail")
    async def gmail_webhook(request: Request, background: BackgroundTasks) -> Response:
        if pubsub is not None:  # OIDC replaces the query token once configured
            if not pubsub.ok(request.headers.get("authorization")):
                return Response(status_code=401)
        elif token is not None:
            given = request.query_params.get("token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return Response(status_code=401)
        push = decode_push(await request.body())
        if push is None:
            log("push malformado; descartado")
        elif syncs is not None:
            background.add_task(route_push, push.emailAddress)
        elif trigger is not None:
            background.add_task(trigger)
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if api is not None:
        mount_web(app, web_dist)  # last: the catch-all must not shadow any route
    return app


def build_app() -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv()

    import threading
    from datetime import UTC, datetime

    from src.alerts.service import FailureStreak, build_alert_service, gmail_revoked_hook
    from src.db.scope import account_scope
    from src.db.store import Store
    from src.gmail.accounts_store import (
        account_email,
        account_id_by_email,
        mark_synced,
        syncable_account_ids,
    )
    from src.gmail.clients import GmailClients, GmailNotConnected, GmailRevoked
    from src.gmail.watch import ensure_watch, renew_due
    from src.jobs.store import JobStore
    from src.services.sync import sync_inbox

    store = Store()
    # The API only ingests and enqueues; the worker process runs the agent.
    jobs = JobStore(store.sf)
    clients = GmailClients(store.sf)
    alerts = build_alert_service(store, clients)
    clients.on_revoked = gmail_revoked_hook(alerts)
    streaks: dict[int, FailureStreak] = {}

    def streak_for(account: int) -> FailureStreak:
        found = streaks.get(account)
        if found is None:
            email = account_email(store.sf, account) or f"conta {account}"
            found = streaks[account] = FailureStreak(
                SYNC_FAILURE_STREAK,
                lambda n, err: alerts.alert(
                    "sync_failures", f"sync de {email} falhou {n} vezes seguidas", err
                ),
            )
        return found

    def run_sync(account: int) -> None:
        # googleapiclient services are not thread-safe: one per sync run
        with account_scope(account):
            try:
                sync_inbox(store, clients.service_for(account), jobs)
            except (GmailRevoked, GmailNotConnected) as exc:
                log(f"sync da conta {account} parou: {exc}")  # the user must reconnect
                return
            except Exception as exc:
                streak_for(account).failure(f"{type(exc).__name__}: {exc}")
                raise
        mark_synced(store.sf, account, datetime.now(UTC))
        streak_for(account).success()

    syncs = SyncCoordinators(run_sync)

    def renew_account(account: int) -> None:
        with account_scope(account):
            ensure_watch(store, clients.service_for(account))

    def start_watch() -> None:
        """Start/renew the watch of every connected account that needs it."""
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        errors = renew_due(store.sf, now_ms, renew_account, RENEW_WITHIN_MS)
        if errors:
            raise RuntimeError("; ".join(errors))

    def renew_and_sync() -> None:
        try:
            start_watch()
        except Exception as exc:
            alerts.alert("watch_failures", "renovação do watch do Gmail falhou", f"{exc}")
            raise
        for account in syncable_account_ids(store.sf):  # safety net for missed pushes
            syncs.trigger(account)

    def on_connected(account: int) -> None:
        """A Gmail was (re)connected: watch it and catch up, off the request."""
        renew_account(account)
        threading.Thread(target=syncs.trigger, args=(account,), daemon=True).start()

    api: ApiDeps | None = None
    if os.getenv("JWT_SECRET"):
        from src.auth.config import load_auth_settings

        api = ApiDeps.build(
            store.sf, load_auth_settings(), alerts=alerts, gmail=clients.getter(),
            on_connected=on_connected, clients=clients,
        )
    else:
        log("JWT_SECRET ausente: API do painel desligada (só webhook e /healthz)")

    audience, service_account = os.getenv("PUBSUB_AUDIENCE"), os.getenv("PUBSUB_SERVICE_ACCOUNT")
    pubsub = PubSubVerifier(audience, service_account) if audience and service_account else None

    return create_app(
        token=os.getenv("WEBHOOK_TOKEN") or None,
        syncs=syncs,
        resolve_account=lambda email: account_id_by_email(store.sf, email),
        pubsub=pubsub,
        on_startup=start_watch,
        renew=renew_and_sync,
        api=api,
        web_dist=Path(os.getenv("WEB_DIST", "web/dist")),
    )
