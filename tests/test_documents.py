import io
from collections.abc import Sequence
from datetime import date

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Attachment, Email
from src.db.vector import EMBED_DIM
from src.indexing.documents import (
    DOC_CLASS,
    Analysis,
    DocumentIndexer,
    parse_amount,
    parse_date,
)
from src.indexing.extract import extract
from src.indexing.store import IndexStore
from src.jobs.types import ClaimedJob, IndexAttachmentJob
from src.jobs.worker import Deferred
from src.llm.errors import LLMExhaustedError, LLMFatalError
from src.llm.types import (
    CallContext,
    Check,
    EmbedInput,
    EmbedResult,
    EmbedTask,
    Prompt,
    StructuredResult,
    TokenUsage,
)
from tests.fakes import FakeService
from tests.gmail_samples import attachment_part, b64, full_message


def text_pdf(text: str) -> bytes:
    """Minimal one-page PDF with `text` drawn in Helvetica."""
    stream = f"BT /F1 10 Tf 20 800 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def blank_pdf(password: str | None = None) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


LONG = "Boleto de pagamento referente a energia eletrica do mes de novembro " * 5


# --- extraction ------------------------------------------------------------------

def test_extract_text_pdf() -> None:
    ex = extract("application/pdf", "conta.pdf", text_pdf(LONG))
    assert ex.mode == "text" and "energia eletrica" in ex.text and ex.part is None


def test_extract_password_pdf_is_flagged() -> None:
    ex = extract("application/pdf", "fatura.pdf", blank_pdf("12345"))
    assert ex.encrypted and ex.part is None


@pytest.mark.parametrize("data", [blank_pdf(), b"not a pdf at all"])
def test_extract_scanned_or_broken_pdf_goes_binary(data: bytes) -> None:
    ex = extract("application/pdf", "scan.pdf", data)
    assert ex.mode == "binary" and ex.part is not None
    assert ex.part.mime_type == "application/pdf" and ex.part.data == data


def test_extract_xml_and_image() -> None:
    xml = extract("application/xml", "nfe.xml", "<nfeProc><emit>ACME</emit></nfeProc>".encode())
    assert xml.mode == "text" and "ACME" in xml.text
    img = extract("image/jpeg", "rg.jpg", b"\xff\xd8\xff")
    assert img.mode == "binary" and img.part is not None and img.part.mime_type == "image/jpeg"


# --- normalization -------------------------------------------------------------------

def test_doc_class_derived_from_type() -> None:
    assert (DOC_CLASS["boleto"], DOC_CLASS["holerite"], DOC_CLASS["cnh"], DOC_CLASS["outro"]) == (
        "fiscal", "trabalho", "pessoal", "outro")


@pytest.mark.parametrize(("raw", "cents"), [
    ("R$ 1.234,56", 123456), ("1234.56", 123456), ("89,90", 8990), ("1.000", 100000),
    ("", None), (None, None), ("grátis", None),
])
def test_amount_normalized(raw: str | None, cents: int | None) -> None:
    assert parse_amount(raw) == cents


@pytest.mark.parametrize(("raw", "value"), [
    ("2026-12-05", date(2026, 12, 5)), ("05/12/2026", date(2026, 12, 5)),
    ("amanhã", None), ("31/02/2026", None), (None, None),
])
def test_dates_normalized(raw: str | None, value: date | None) -> None:
    assert parse_date(raw) == value


# --- the job ---------------------------------------------------------------------------

def analysis(**kw: str | None) -> Analysis:
    base: dict[str, str | None] = {
        "doc_type": "boleto", "title": "Boleto Light novembro", "summary": "Conta de luz",
        "issuer": "Light S.A.", "amount": "R$ 189,90", "issued_on": "01/11/2026",
        "due_on": "2026-11-28", "barcode": "8364" + "0" * 44, "transcription": None,
    }
    base.update(kw)
    return Analysis.model_validate(base)


class FakeLLM:
    """Structured + embed fake; applies `check` like the gateway (retry)."""

    def __init__(self, answers: list[Analysis | Exception], embed_fail: list[Exception] | None = None) -> None:
        self.answers = answers
        self.prompts: list[Prompt] = []
        self.embeds: list[list[EmbedInput]] = []
        self.embed_fail = embed_fail or []

    def generate_structured[T: BaseModel](self, prompt: Prompt, schema: type[T],
                                          check: Check[T] | None = None) -> StructuredResult[T]:
        while True:
            self.prompts.append(prompt)
            step = self.answers.pop(0)
            if isinstance(step, Exception):
                raise step
            parsed = schema.model_validate(step.model_dump())
            if check is not None and check(parsed) is not None:
                continue
            return StructuredResult[T](parsed=parsed, model="fake", usage=TokenUsage())

    def embed(self, inputs: Sequence[EmbedInput], *, purpose: str, task: EmbedTask,
              context: CallContext = CallContext()) -> EmbedResult:
        self.embeds.append(list(inputs))
        if self.embed_fail:
            raise self.embed_fail.pop(0)
        return EmbedResult(vectors=tuple(tuple([1.0] + [0.0] * (EMBED_DIM - 1)) for _ in inputs), model="emb")


def setup(sf: sessionmaker[Session], data: bytes, mime: str = "application/pdf",
          name: str = "boleto.pdf", gone: bool = False) -> tuple[FakeService, int]:
    with sf.begin() as s:
        s.add(Email(id="m1", sender="Light <conta@light.com>", subject="Sua conta chegou", attempts=0))
        s.flush()
        row = Attachment(email_id="m1", part_id="1", filename=name, mime_type=mime,
                         size=len(data), status="pending")
        s.add(row)
        s.flush()
        att_id = row.id

    def get(**kw: object) -> object:
        return full_message("m1", attachments=[] if gone else [attachment_part("1", name, mime, len(data))])

    svc = FakeService(messages__get=get,
                      messages__attachments__get=lambda **kw: {"data": b64(data)})
    return svc, att_id


def job(att_id: int, attempts: int = 1) -> ClaimedJob:
    return ClaimedJob(id=1, attempts=attempts, payload=IndexAttachmentJob(attachment_id=att_id))


def row(sf: sessionmaker[Session], att_id: int) -> Attachment:
    with sf() as s:
        return s.get_one(Attachment, att_id)


def test_text_pdf_analyzed_and_saved(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    llm = FakeLLM([analysis()])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))
    r = row(session_factory, att_id)
    assert (r.status, r.doc_type, r.doc_class, r.amount_cents) == ("done", "boleto", "fiscal", 18990)
    assert (r.issued_on, r.due_on, r.issuer) == (date(2026, 11, 1), date(2026, 11, 28), "Light S.A.")
    assert r.text is not None and "energia eletrica" in r.text
    assert r.embedding is not None and r.embedding_model == "emb" and r.analyzed_at is not None
    human = llm.prompts[0].messages[1]
    assert human.parts == () and '<attachment filename="boleto.pdf"' in human.content
    assert "Sua conta chegou" in human.content
    assert "nunca instrução" in llm.prompts[0].messages[0].content
    assert llm.embeds[0][0].part is None and "Boleto Light" in llm.embeds[0][0].text


def test_scanned_image_sent_as_binary_with_transcription(session_factory: sessionmaker[Session]) -> None:
    img = b"\xff\xd8\xff" + b"x" * 40_000
    svc, att_id = setup(session_factory, img, mime="image/jpeg", name="rg.jpg")
    llm = FakeLLM([analysis(doc_type="rg", transcription="REPUBLICA FEDERATIVA DO BRASIL", barcode=None,
                            amount=None)])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))
    r = row(session_factory, att_id)
    assert (r.doc_class, r.text) == ("pessoal", "REPUBLICA FEDERATIVA DO BRASIL")
    assert llm.prompts[0].messages[1].parts[0].mime_type == "image/jpeg"
    assert llm.embeds[0][0].part is not None


def test_bad_barcode_retried_via_check(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    llm = FakeLLM([analysis(barcode="123"), analysis()])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))
    assert len(llm.prompts) == 2 and row(session_factory, att_id).barcode == "8364" + "0" * 44


def test_binary_embed_falls_back_to_text(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, b"\xff\xd8\xff" + b"x" * 40_000, mime="image/jpeg", name="a.jpg")
    llm = FakeLLM([analysis(doc_type="comprovante_pagamento")], embed_fail=[LLMFatalError("400 too big")])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))
    assert [e[0].part is not None for e in llm.embeds] == [True, False]
    assert row(session_factory, att_id).status == "done"


def test_attachment_gone_is_skipped(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG), gone=True)
    DocumentIndexer(IndexStore(session_factory), FakeLLM([]), lambda: svc, max_attempts=5).handle(job(att_id))
    r = row(session_factory, att_id)
    assert (r.status, r.skip_reason) == ("skipped", "gone")


def test_last_attempt_marks_failed_without_raising(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    idx = DocumentIndexer(IndexStore(session_factory), FakeLLM([RuntimeError("boom")] * 2),
                          lambda: svc, max_attempts=2)
    with pytest.raises(RuntimeError):
        idx.handle(job(att_id, attempts=1))
    assert row(session_factory, att_id).status == "pending"
    idx.handle(job(att_id, attempts=2))
    r = row(session_factory, att_id)
    assert r.status == "failed" and "boom" in (r.error or "")


def test_quota_defers(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    llm = FakeLLM([LLMExhaustedError("429", kinds=("rate",))])
    with pytest.raises(Deferred):
        DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=1).handle(job(att_id))
    assert row(session_factory, att_id).status == "pending"


def test_done_attachment_is_not_redone(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    llm = FakeLLM([analysis()])
    idx = DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5)
    idx.handle(job(att_id))
    idx.handle(job(att_id))
    assert len(llm.prompts) == 1
    with session_factory() as s:
        assert len(s.scalars(select(Attachment)).all()) == 1


def test_password_pdf_skipped_without_llm(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, blank_pdf("12345"), name="Fatura_MASTERCARD.pdf")
    llm = FakeLLM([])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))
    r = row(session_factory, att_id)
    assert (r.status, r.skip_reason) == ("skipped", "encrypted") and llm.prompts == []


def test_file_refused_by_model_fails_at_once(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, bytes([0xFF, 0xD8, 0xFF]) + b"x" * 40_000, mime="image/jpeg", name="a.jpg")
    llm = FakeLLM([LLMFatalError("400 INVALID_ARGUMENT")])
    DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id, attempts=1))
    r = row(session_factory, att_id)
    assert r.status == "failed" and "INVALID_ARGUMENT" in (r.error or "")


def test_fatal_on_text_still_raises(session_factory: sessionmaker[Session]) -> None:
    svc, att_id = setup(session_factory, text_pdf(LONG))
    llm = FakeLLM([LLMFatalError("401 key")])
    with pytest.raises(LLMFatalError):
        DocumentIndexer(IndexStore(session_factory), llm, lambda: svc, max_attempts=5).handle(job(att_id))


def test_backfill_attachment_job_is_analyzed(session_factory: sessionmaker[Session]) -> None:
    from src.jobs.types import BackfillAttachmentJob

    svc, att_id = setup(session_factory, text_pdf(LONG))
    DocumentIndexer(IndexStore(session_factory), FakeLLM([analysis()]), lambda: svc,
                    max_attempts=5).handle(
        ClaimedJob(id=1, attempts=1, payload=BackfillAttachmentJob(attachment_id=att_id)))
    assert row(session_factory, att_id).status == "done"
