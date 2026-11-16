"""FastAPI app: receives Gmail Pub/Sub push notifications.

Run with: uvicorn src.api.app:build_app --factory --workers 1
(single worker: the sync lock and the renewal scheduler live in-process).
"""
import asyncio
import base64
import binascii
import hmac
import json
import os
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import BackgroundTasks, FastAPI, Request, Response

from src.api.log_filters import install_access_log_filter
from src.utils.console import log




def decode_push(body):
    """Decode a Pub/Sub push body into {"emailAddress", "historyId"}, or None."""
    try:
        data = base64.b64decode(body["message"]["data"], validate=True)
        payload = json.loads(data)
        return {
            "emailAddress": payload["emailAddress"],
            "historyId": str(payload["historyId"]),
        }
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None


async def _renew_loop(renew, interval_s, retry_s):
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
    async def lifespan(app):
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
    async def gmail_webhook(request: Request, background: BackgroundTasks):
        if token is not None:
            given = request.query_params.get("token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return Response(status_code=401)
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        if decode_push(body) is None:
            log("push malformado; descartado")
        else:
            background.add_task(trigger)
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


def build_app() -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv()

    from src.classification.classifier import Classifier
    from src.db.store import Store
    from src.gmail.client import get_gmail_service
    from src.gmail.watch import ensure_watch
    from src.services.sync import SyncCoordinator, sync_inbox

    store = Store()
    classifier = None

    def run_sync():
        nonlocal classifier
        if classifier is None:
            classifier = Classifier()
        # googleapiclient services are not thread-safe: one per sync run.
        sync_inbox(store, get_gmail_service(), classifier)

    coordinator = SyncCoordinator(run_sync)

    def start_watch():
        ensure_watch(store, get_gmail_service())

    def renew_and_sync():
        start_watch()
        coordinator.trigger()

    return create_app(
        trigger=coordinator.trigger,
        token=os.getenv("WEBHOOK_TOKEN") or None,
        on_startup=start_watch,
        renew=renew_and_sync,
    )
