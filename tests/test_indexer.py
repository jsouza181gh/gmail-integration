from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.scope import email_key
from src.db.models import Attachment, Email, EmailEmbedding, Job
from src.db.vector import EMBED_DIM
from src.indexing.indexer import Indexer, candidate
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, IndexEmailJob
from src.jobs.worker import Deferred
from src.llm.errors import LLMExhaustedError
from src.llm.types import CallContext, EmbedInput, EmbedResult, EmbedTask
from tests.fakes import FakeService, http_error
from tests.gmail_samples import attachment_part, full_message


class FakeEmbedder:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[list[EmbedInput]] = []
        self.contexts: list[CallContext] = []
        self.fail = fail

    def embed(self, inputs: Sequence[EmbedInput], *, purpose: str, task: EmbedTask,
              context: CallContext = CallContext()) -> EmbedResult:
        if self.fail is not None:
            raise self.fail
        self.calls.append(list(inputs))
        self.contexts.append(context)
        vec = tuple(1.0 if j == 0 else 0.0 for j in range(EMBED_DIM))
        return EmbedResult(vectors=tuple(vec for _ in inputs), model="emb")


def service(messages: dict[str, dict[str, object]], gone: Sequence[str] = ()) -> FakeService:
    def get(**kw: object) -> object:
        mid = str(kw["id"])
        if mid in gone:
            raise http_error(404)
        return messages[mid]

    return FakeService(messages__get=get)


def make(sf: sessionmaker[Session], svc: FakeService, emb: FakeEmbedder) -> tuple[Indexer, JobStore]:
    jobs = JobStore(sf)
    return Indexer(IndexStore(sf), jobs, emb, lambda: svc), jobs


def mail() -> dict[str, dict[str, object]]:
    return {
        "a": full_message("a", body="Segue o boleto de novembro", attachments=[
            attachment_part("1", "boleto.pdf", "application/pdf", 80_000),
            attachment_part("2", "logo.png", "image/png", 3_000, inline_cid=True),
            attachment_part("3", "planilha.xlsx", "application/vnd.ms-excel", 9_000),
        ]),
        "b": full_message("b", body="Oi"),
        "c": full_message("c", body="Holerite", attachments=[
            attachment_part("1", "holerite.jpg", "image/jpeg", 200_000),
        ]),
    }


def test_index_saves_body_attachments_and_embeds_once(session_factory: sessionmaker[Session]) -> None:
    emb = FakeEmbedder()
    idx, jobs = make(session_factory, service(mail()), emb)
    out = idx.index(["a", "b", "c"], run_id=None)
    assert (out.indexed, out.embedded, out.attachments_enqueued) == (3, 3, 2)
    assert len(emb.calls) == 1 and len(emb.calls[0]) == 3
    assert emb.calls[0][0].text.startswith("De: Loja <a@x.com>\nAssunto: assunto a\n")
    assert "Segue o boleto" in emb.calls[0][0].text
    with session_factory() as s:
        a = s.get_one(Email, email_key("a"))
        atts = {x.filename: x for x in s.scalars(select(Attachment))}
        kinds = sorted(s.scalars(select(Job.kind)))
        assert s.scalars(select(EmailEmbedding.email_id)).all() == ["a", "b", "c"]
    assert a.body == "Segue o boleto de novembro" and a.indexed_at is not None
    assert atts["boleto.pdf"].status == "pending"
    assert (atts["logo.png"].status, atts["logo.png"].skip_reason, atts["logo.png"].doc_class) == (
        "skipped", "inline", "outro")
    assert atts["planilha.xlsx"].skip_reason == "unsupported_type"
    assert kinds == ["index_attachment", "index_attachment"]


def test_index_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    emb = FakeEmbedder()
    svc = service(mail())
    idx, _ = make(session_factory, svc, emb)
    idx.index(["a", "b"], run_id=None)
    calls = len(svc.calls)
    again = idx.index(["a", "b"], run_id=None)
    assert (again.indexed, again.embedded) == (0, 0)
    assert len(svc.calls) == calls and len(emb.calls) == 1


def test_index_links_backfill_run(session_factory: sessionmaker[Session]) -> None:
    from src.db.store import Store

    run_id = Store(session_factory).get_or_create_run("backfill:x").id
    emb = FakeEmbedder()
    idx, _ = make(session_factory, service(mail()), emb)
    idx.index(["b"], run_id=run_id)
    assert Store(session_factory).count_pending(run_id) == 1  # linked, not classified yet
    assert emb.contexts[0].run_id == run_id and emb.contexts[0].email_ids == ("b",)


def test_message_gone_is_skipped(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        s.add(Email(id="a", attempts=0))
    idx, _ = make(session_factory, service(mail(), gone=["a"]), FakeEmbedder())
    out = idx.index(["a", "b"], run_id=None)
    assert (out.gone, out.indexed) == (1, 1)
    with session_factory() as s:
        assert s.get_one(Email, email_key("a")).index_error == "gone"
    assert idx.index(["a"], run_id=None).gone == 0  # not fetched again


def test_system_mail_never_ingested(session_factory: sessionmaker[Session]) -> None:
    msgs = {"s": full_message("s", system=True)}
    idx, _ = make(session_factory, service(msgs), FakeEmbedder())
    out = idx.index(["s"], run_id=None)
    assert (out.system, out.indexed) == (1, 0)
    with session_factory() as s:
        assert s.get(Email, email_key("s")) is None


def test_quota_exhausted_defers(session_factory: sessionmaker[Session]) -> None:
    emb = FakeEmbedder(fail=LLMExhaustedError("429", kinds=("rate",)))
    idx, _ = make(session_factory, service(mail()), emb)
    job = ClaimedJob(id=1, account_id=1, attempts=1, payload=IndexEmailJob(email_id="b"))
    with pytest.raises(Deferred):
        idx.handle(job)


def test_other_exhaustion_fails_normally(session_factory: sessionmaker[Session]) -> None:
    emb = FakeEmbedder(fail=LLMExhaustedError("bad", kinds=("bad_output",)))
    idx, _ = make(session_factory, service(mail()), emb)
    with pytest.raises(LLMExhaustedError):
        idx.handle(ClaimedJob(id=1, account_id=1, attempts=1, payload=IndexEmailJob(email_id="b")))


@pytest.mark.parametrize(("mime", "name", "size", "inline", "reason"), [
    ("application/pdf", "a.pdf", 10_000, False, None),
    ("application/octet-stream", "NF.PDF", 10_000, False, None),
    ("application/octet-stream", "nfe.xml", 10_000, False, None),
    ("text/xml", "nfe.xml", 10_000, False, None),
    ("image/jpeg", "rg.jpg", 150_000, False, None),
    ("image/png", "tiny.png", 20_000, False, "small_image"),
    ("image/png", "logo.png", 90_000, True, "inline"),
    ("application/pdf", "huge.pdf", 16_000_000, False, "too_large"),
    ("image/jpeg", "scan.jpg", 11_000_000, False, "too_large"),
    ("application/zip", "x.zip", 10_000, False, "unsupported_type"),
])
def test_candidate_rules(mime: str, name: str, size: int, inline: bool, reason: str | None) -> None:
    from src.gmail.full import AttachmentRef

    ref = AttachmentRef(part_id="1", filename=name, mime_type=mime, size=size, inline=inline)
    assert candidate(ref) == reason


def test_backfill_enqueues_backfill_attachments(session_factory: sessionmaker[Session]) -> None:
    idx, _ = make(session_factory, service(mail()), FakeEmbedder())
    idx.index(["a", "b", "c"], run_id=7)
    with session_factory() as s:
        kinds = sorted(s.scalars(select(Job.kind)))
    assert kinds == ["backfill_attachment", "backfill_attachment"]
