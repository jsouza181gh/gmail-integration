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

from src.api.log_filters import install_access_log_filter
from src.utils.console import log


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
) -> FastAPI:
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

    app = FastAPI(lifespan=lifespan)

    @app.post("/webhooks/gmail")
    async def gmail_webhook(request: Request, background: BackgroundTasks) -> Response:
        if token is not None:
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

    store = Store()
    # The API only ingests and enqueues; the worker process runs the agent.
    jobs = JobStore(store.sf)

    def run_sync() -> None:
        # googleapiclient services are not thread-safe: one per sync run.
        sync_inbox(store, get_gmail_service(), jobs)

    coordinator = SyncCoordinator(run_sync)

    def start_watch() -> None:
        ensure_watch(store, get_gmail_service())

    def renew_and_sync() -> None:
        start_watch()
        coordinator.trigger()

    return create_app(
        trigger=coordinator.trigger,
        token=os.getenv("WEBHOOK_TOKEN") or None,
        on_startup=start_watch,
        renew=renew_and_sync,
    )
