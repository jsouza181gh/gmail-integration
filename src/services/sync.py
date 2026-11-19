"""Incremental sync: Gmail history -> emails table -> classification."""
import threading
from typing import Callable

from googleapiclient.errors import HttpError

from src.classification.classifier import Abort, classify_pending
from src.gmail.messages import message_summary
from src.utils.console import log

USER_ID = "me"
WEBHOOK_QUERY = "webhook:inbox"
FALLBACK_DAYS = 7


class HistoryExpired(Exception):
    """startHistoryId is too old; Gmail no longer has the history."""


def list_new_message_ids(service, start_history_id):
    """Ids of messages added to INBOX since start_history_id, plus the newest
    historyId seen."""
    ids, latest, page_token = [], start_history_id, None
    while True:
        params = {
            "userId": USER_ID,
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
            "labelId": "INBOX",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            result = service.users().history().list(**params).execute(num_retries=5)
        except HttpError as e:
            if e.resp.status == 404:
                raise HistoryExpired(str(e)) from e
            raise
        for record in result.get("history", []):
            for added in record.get("messagesAdded", []):
                mid = added["message"]["id"]
                if mid not in ids:
                    ids.append(mid)
        latest = result.get("historyId", latest)
        page_token = result.get("nextPageToken")
        if not page_token:
            return ids, latest


def list_recent_inbox_ids(service, days=FALLBACK_DAYS):
    """Fallback when the history cursor expired: recent INBOX ids and the
    mailbox's current historyId (the new cursor)."""
    ids, page_token = [], None
    while True:
        params = {"userId": USER_ID, "q": f"in:inbox newer_than:{days}d"}
        if page_token:
            params["pageToken"] = page_token
        result = service.users().messages().list(**params).execute(num_retries=5)
        ids.extend(m["id"] for m in result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    profile = service.users().getProfile(userId=USER_ID).execute(num_retries=5)
    return ids, profile["historyId"]


def _summary_or_none(service, message_id):
    try:
        return message_summary(service, message_id)
    except HttpError as e:
        if e.resp.status == 404:  # deleted between history and fetch
            log(f"mensagem {message_id} não existe mais; ignorando")
            return None
        raise


def sync_inbox(store, service, classifier):
    """Ingest new INBOX mail, advance the cursor, classify what is pending.

    The cursor moves right after ingest: from then on the emails are durable
    and any classification failure is retried through the pending rows.
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

    try:
        classify_pending(store, classifier, run_id)
    except Abort as exc:
        log(f"classificação interrompida: {exc}; pendentes ficam para o próximo sync")

    return {"ingested": len(new_emails), "pending": store.count_pending(run_id)}


class SyncCoordinator:
    """Runs run_fn one at a time; triggers that arrive meanwhile coalesce
    into a single extra run."""

    def __init__(self, run_fn: Callable[[], None]):
        self.run_fn = run_fn
        self._guard = threading.Lock()
        self._running = False
        self._dirty = False

    def trigger(self):
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
