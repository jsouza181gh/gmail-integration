"""Incremental sync: Gmail history -> emails table -> one agent job per new email."""
import threading
from collections.abc import Callable

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from src.db.store import Store
from src.gmail.messages import MessageSummary, message_summary
from src.jobs.store import JobStore
from src.jobs.types import ProcessEmailJob
from src.llm.types import Schema
from src.utils.console import log

USER_ID = "me"
WEBHOOK_QUERY = "webhook:inbox"
FALLBACK_DAYS = 7


class HistoryExpired(Exception):
    """startHistoryId is too old; Gmail no longer has the history."""


class SyncResult(Schema):
    ingested: int
    enqueued: int


# --- slices of Gmail API responses we read ---------------------------------

class _MessageRef(BaseModel):
    id: str


class _Added(BaseModel):
    message: _MessageRef


class _HistoryRecord(BaseModel):
    messagesAdded: list[_Added] = []


class _HistoryPage(BaseModel):
    history: list[_HistoryRecord] = []
    historyId: str | None = None
    nextPageToken: str | None = None


class _ListPage(BaseModel):
    messages: list[_MessageRef] = []
    nextPageToken: str | None = None


class _Profile(BaseModel):
    historyId: str


def list_new_message_ids(service: Resource, start_history_id: str) -> tuple[list[str], str]:
    """Ids of messages added to INBOX since start_history_id, plus the newest
    historyId seen."""
    ids: list[str] = []
    latest, page_token = start_history_id, None
    while True:
        params: dict[str, str | list[str]] = {
            "userId": USER_ID,
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
            "labelId": "INBOX",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            raw = service.users().history().list(**params).execute(num_retries=5)
        except HttpError as e:
            if e.resp.status == 404:
                raise HistoryExpired(str(e)) from e
            raise
        page = _HistoryPage.model_validate(raw)
        for record in page.history:
            for added in record.messagesAdded:
                if added.message.id not in ids:
                    ids.append(added.message.id)
        latest = page.historyId or latest
        page_token = page.nextPageToken
        if not page_token:
            return ids, latest


def list_recent_inbox_ids(service: Resource, days: int = FALLBACK_DAYS) -> tuple[list[str], str]:
    """Fallback when the history cursor expired: recent INBOX ids and the
    mailbox's current historyId (the new cursor)."""
    ids: list[str] = []
    page_token: str | None = None
    while True:
        params = {"userId": USER_ID, "q": f"in:inbox newer_than:{days}d"}
        if page_token:
            params["pageToken"] = page_token
        page = _ListPage.model_validate(
            service.users().messages().list(**params).execute(num_retries=5)
        )
        ids.extend(m.id for m in page.messages)
        page_token = page.nextPageToken
        if not page_token:
            break
    profile = _Profile.model_validate(
        service.users().getProfile(userId=USER_ID).execute(num_retries=5)
    )
    return ids, profile.historyId


def _summary_or_none(service: Resource, message_id: str) -> MessageSummary | None:
    try:
        return message_summary(service, message_id)
    except HttpError as e:
        if e.resp.status == 404:  # deleted between history and fetch
            log(f"mensagem {message_id} não existe mais; ignorando")
            return None
        raise


def sync_inbox(store: Store, service: Resource, jobs: JobStore) -> SyncResult:
    """Ingest new INBOX mail, advance the cursor, enqueue `process_email` jobs.

    The cursor moves right after ingest: from then on the emails are durable.
    Enqueueing is idempotent per email, so a repeated push never duplicates work.
    """
    state = store.get_watch_state()
    if state is None:
        raise RuntimeError("estado do watch ausente; ensure_watch ainda não rodou")

    try:
        ids, latest = list_new_message_ids(service, state.history_id)
    except HistoryExpired:
        log("historyId expirado; fallback para os últimos dias da INBOX")
        ids, latest = list_recent_inbox_ids(service)

    run_id = store.get_or_create_run(WEBHOOK_QUERY).id
    known = store.known_ids(ids)
    summaries = [_summary_or_none(service, i) for i in ids if i not in known]
    new_emails = [s for s in summaries if s]
    store.ingest(run_id, [e.id for e in new_emails] + list(known), new_emails)
    store.set_history_id(latest)

    enqueued = sum(jobs.enqueue(ProcessEmailJob(email_id=e.id)) for e in new_emails)
    return SyncResult(ingested=len(new_emails), enqueued=enqueued)


class SyncCoordinator:
    """Runs run_fn one at a time; triggers that arrive meanwhile coalesce
    into a single extra run."""

    def __init__(self, run_fn: Callable[[], None]) -> None:
        self.run_fn = run_fn
        self._guard = threading.Lock()
        self._running = False
        self._dirty = False

    def trigger(self) -> None:
        with self._guard:
            self._dirty = True
            if self._running:
                return
            self._running = True
        while True:
            with self._guard:
                if not self._dirty:
                    self._running = False
                    return
                self._dirty = False
            try:
                self.run_fn()
            except Exception as exc:  # noqa: BLE001
                log(f"sync falhou: {type(exc).__name__}: {exc}")
