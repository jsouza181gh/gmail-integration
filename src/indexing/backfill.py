"""History backfill: a chain of `backfill_page` jobs, newest mail first.

Each job handles one page of 25 messages: index (body, attachments,
vectors), classify, then advance the cursor (`runs` row) and enqueue the
next page. Everything before the cursor moves is idempotent, so a job cut
short by a deploy simply runs again; a job whose page is already behind the
cursor re-enqueues the current page, which heals a chain broken between
"cursor saved" and "next job enqueued".

The backfill never proposes actions: no rules, no agent loop, no labels.
"""
from collections.abc import Callable, Sequence
from typing import Protocol

from googleapiclient.discovery import Resource
from pydantic import BaseModel

from src.agent.settings import RuntimeSettings
from src.alerts.service import AlertKind
from src.classification.classifier import (
    GIVE_UP_CATEGORY,
    MAX_EMAIL_ATTEMPTS,
    EmailClassifier,
)
from src.db.rows import RunRow
from src.db.store import Store
from src.indexing.indexer import Indexer, defer_on_quota
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.jobs.types import BackfillPageJob, ClaimedJob, JobCounts
from src.jobs.worker import Deferred
from src.llm.errors import LLMExhaustedError
from src.llm.types import Schema
from src.utils.console import log

BACKFILL_QUERY = "-in:chats -in:drafts"
# Paused from the panel: the chain waits and re-checks every 5 min.
PAUSE_RECHECK_S = 300
PAUSED_REASON = "backfill pausado"
RUN_QUERY = f"backfill:{BACKFILL_QUERY}"
PAGE_SIZE = 25
USER_ID = "me"


class Alerter(Protocol):
    def alert(self, kind: AlertKind, subject: str, body: str) -> object: ...


class BackfillProgress(Schema):
    run_id: int
    pages: int
    fetch_done: bool
    emails: int
    classified: int
    indexed: int
    embedded: int
    attachments: dict[str, int]
    jobs: dict[str, JobCounts]


class _Ref(BaseModel):
    id: str


class _ListPage(BaseModel):
    messages: list[_Ref] = []
    nextPageToken: str | None = None


class Backfill:
    def __init__(
        self,
        store: Store,
        index_store: IndexStore,
        indexer: Indexer,
        classifier: EmailClassifier,
        jobs: JobStore,
        alerts: Alerter,
        service_factory: Callable[[], Resource],
    ) -> None:
        self.store = store
        self.index_store = index_store
        self.indexer = indexer
        self.classifier = classifier
        self.jobs = jobs
        self.alerts = alerts
        self._service = service_factory

    def run(self) -> RunRow:
        return self.index_store.backfill_run(RUN_QUERY)

    def start(self) -> BackfillProgress:
        """Create the run if needed and make sure the current page is queued.

        Safe to repeat at any time: it also repairs a broken chain.
        """
        run = self.run()
        if not run.fetch_done:
            self.jobs.enqueue(BackfillPageJob(run_id=run.id, page_no=run.pages))
        return self.progress()

    def progress(self) -> BackfillProgress:
        run = self.run()
        counts = self.index_store.run_counts(run.id)
        return BackfillProgress(
            run_id=run.id,
            pages=run.pages,
            fetch_done=run.fetch_done,
            **counts.model_dump(),
            jobs=self.jobs.counts_by_kind(),
        )

    def handle(self, job: ClaimedJob) -> None:
        payload = job.payload
        if not isinstance(payload, BackfillPageJob):
            raise TypeError(f"job {job.id} não é backfill_page")
        run = self.store.get_run(payload.run_id)
        if run.fetch_done:
            return
        if RuntimeSettings(self.store.sf).backfill_paused():
            raise Deferred(PAUSE_RECHECK_S, PAUSED_REASON)
        if payload.page_no < run.pages:  # already done; make sure the chain goes on
            self.jobs.enqueue(BackfillPageJob(run_id=run.id, page_no=run.pages))
            return
        if payload.page_no > run.pages:
            log(f"backfill: job da página {payload.page_no} fora de ordem (cursor em {run.pages})")
            return

        page = self._list(run.page_token)
        ids = [m.id for m in page.messages]
        try:
            outcome = self.indexer.index(ids, run.id)
            self._classify(ids)
        except LLMExhaustedError as exc:
            deferred = defer_on_quota(exc)
            if deferred is None:
                raise
            raise deferred from exc

        known = self.store.known_ids(ids)
        kept = [i for i in ids if i in known]
        self.store.save_page(run.id, kept, [], page.nextPageToken)
        log(
            f"backfill página {payload.page_no}: {len(ids)} mensagens, "
            f"{outcome.indexed} indexadas, {outcome.attachments_enqueued} anexo(s) na fila"
        )
        if page.nextPageToken:
            self.jobs.enqueue(BackfillPageJob(run_id=run.id, page_no=payload.page_no + 1))
        else:
            p = self.progress()
            self.alerts.alert(
                "backfill_done",
                "[gmail-integration] backfill concluído",
                f"{p.pages} páginas, {p.emails} e-mails ({p.classified} classificados, "
                f"{p.embedded} com vetor); anexos: {p.attachments}",
            )

    def _list(self, page_token: str | None) -> _ListPage:
        params: dict[str, str | int | bool] = {
            "userId": USER_ID,
            "q": BACKFILL_QUERY,
            "maxResults": PAGE_SIZE,
            "includeSpamTrash": False,
        }
        if page_token:
            params["pageToken"] = page_token
        return _ListPage.model_validate(
            self._service().users().messages().list(**params).execute(num_retries=5)
        )

    def _classify(self, ids: Sequence[str]) -> None:
        """Classify what is still unclassified; retry misses, then give up."""
        for _ in range(MAX_EMAIL_ATTEMPTS):
            rows = self.index_store.unclassified(ids)
            if not rows:
                return
            results = self.classifier.classify(rows, self.index_store.bodies([r.id for r in rows]))
            self.store.save_classifications(results)
            missing = [r.id for r in rows if r.id not in results]
            self.store.bump_attempts(missing, "sem resposta válida do modelo")
        self.store.give_up(MAX_EMAIL_ATTEMPTS, GIVE_UP_CATEGORY, "modelo não respondeu")
