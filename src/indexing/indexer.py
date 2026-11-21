"""Index emails: full fetch -> body + attachment refs -> email embeddings.

Shared by live mail (`index_email` jobs from the sync) and the backfill.
Idempotent per email: what is already stored is never fetched or embedded
again, so a job can be repeated after any interruption.
"""
from collections.abc import Callable, Sequence

from googleapiclient.discovery import Resource

from src.gmail.full import AttachmentRef, fetch_full
from src.indexing.store import EmailText, IndexStore, NewAttachment
from src.jobs.store import JobStore
from src.jobs.types import BackfillAttachmentJob, ClaimedJob, IndexAttachmentJob, IndexEmailJob
from src.jobs.worker import Deferred
from src.llm.errors import LLMExhaustedError, quota_exhausted
from src.llm.types import CallContext, Embedder, EmbedInput, Schema
from src.utils.console import log

EMBED_TEXT_MAX = 8000
QUOTA_DEFER_S = 1800
SMALL_IMAGE_BYTES = 30_000
# inline request limit is 20 MB after base64 (+33%): keep files well under it
MAX_ATTACHMENT_BYTES = 10_000_000
PDF = "application/pdf"
XML_TYPES = ("application/xml", "text/xml")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")


class IndexOutcome(Schema):
    indexed: int = 0
    gone: int = 0
    system: int = 0
    attachments_enqueued: int = 0
    embedded: int = 0


def mime_for(mime_type: str, filename: str) -> str:
    """Mime type, trusting the extension when the sender said octet-stream."""
    name = filename.lower()
    if mime_type in ("application/octet-stream", "binary/octet-stream", ""):
        if name.endswith(".pdf"):
            return PDF
        if name.endswith(".xml"):
            return "application/xml"
    return mime_type


def effective_mime(ref: AttachmentRef) -> str:
    return mime_for(ref.mime_type, ref.filename)


def candidate(ref: AttachmentRef) -> str | None:
    """None when the attachment is worth analyzing, else why it is `outro`."""
    mime = effective_mime(ref)
    if ref.inline:
        return "inline"
    if mime not in (PDF, *XML_TYPES, *IMAGE_TYPES):
        return "unsupported_type"
    if ref.size > MAX_ATTACHMENT_BYTES:
        return "too_large"
    if mime in IMAGE_TYPES and ref.size < SMALL_IMAGE_BYTES:
        return "small_image"
    return None


def embed_text(e: EmailText) -> str:
    head = f"De: {e.sender or ''}\nAssunto: {e.subject or ''}\nData: {e.date or ''}\n\n"
    return (head + e.body)[:EMBED_TEXT_MAX]


def defer_on_quota(exc: LLMExhaustedError) -> Deferred | None:
    if quota_exhausted(exc):
        return Deferred(QUOTA_DEFER_S, f"cota do LLM esgotada: {exc}")
    return None


class Indexer:
    def __init__(
        self,
        index_store: IndexStore,
        jobs: JobStore,
        embedder: Embedder,
        service_factory: Callable[[], Resource],
    ) -> None:
        self.store = index_store
        self.jobs = jobs
        self.embedder = embedder
        self._service = service_factory

    def index(self, ids: Sequence[str], run_id: int | None) -> IndexOutcome:
        out = IndexOutcome()
        todo = self.store.needs_index(ids)
        if todo:
            service = self._service()
            for email_id in todo:
                full = fetch_full(service, email_id)
                if full is None:
                    log(f"mensagem {email_id} não existe mais; ignorando")
                    self.store.mark_gone(email_id)
                    out = out.model_copy(update={"gone": out.gone + 1})
                    continue
                if full.summary.system:  # our own alerts never feed the index
                    out = out.model_copy(update={"system": out.system + 1})
                    continue
                atts = [NewAttachment(ref=a, skip_reason=candidate(a)) for a in full.attachments]
                pending = self.store.save_indexed(full, atts, run_id)
                # history attachments go to the backfill lane/key, new mail's to the app's
                enqueued = sum(
                    self.jobs.enqueue(
                        BackfillAttachmentJob(attachment_id=a) if run_id is not None
                        else IndexAttachmentJob(attachment_id=a)
                    )
                    for a in pending
                )
                out = out.model_copy(update={
                    "indexed": out.indexed + 1,
                    "attachments_enqueued": out.attachments_enqueued + enqueued,
                })

        texts = self.store.needs_embedding(ids)
        if texts:
            result = self.embedder.embed(
                [EmbedInput(text=embed_text(t)) for t in texts],
                purpose="embed_email",
                task="RETRIEVAL_DOCUMENT",
                context=CallContext(run_id=run_id, email_ids=tuple(t.id for t in texts)),
            )
            self.store.save_email_embeddings(
                result.model, {t.id: v for t, v in zip(texts, result.vectors, strict=True)}
            )
            out = out.model_copy(update={"embedded": len(texts)})
        return out

    def handle(self, job: ClaimedJob) -> None:
        payload = job.payload
        if not isinstance(payload, IndexEmailJob):
            raise TypeError(f"job {job.id} não é index_email")
        try:
            self.index([payload.email_id], run_id=None)
        except LLMExhaustedError as exc:
            deferred = defer_on_quota(exc)
            if deferred is None:
                raise
            raise deferred from exc
