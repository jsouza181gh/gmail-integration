from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.cli.docs import download, list_documents
from src.db.models import Attachment, Email
from src.indexing.store import IndexStore
from tests.fakes import FakeService
from tests.gmail_samples import attachment_part, b64, full_message


@pytest.fixture
def docs(session_factory: sessionmaker[Session]) -> IndexStore:
    with session_factory.begin() as s:
        s.add(Email(id="m1", attempts=0))
        s.flush()
        for part, cls, typ, status in [("1", "fiscal", "boleto", "done"), ("2", "pessoal", "cnh", "done"),
                                       ("3", None, None, "pending")]:
            s.add(Attachment(email_id="m1", part_id=part, filename=f"f{part}.pdf",
                             mime_type="application/pdf", size=10, status=status,
                             doc_class=cls, doc_type=typ, title=f"doc {part}"))
    return IndexStore(session_factory)


def test_docs_list_filters_by_class(docs: IndexStore) -> None:
    out = list_documents(docs, doc_class="fiscal", doc_type=None, limit=10)
    assert "doc 1" in out and "doc 2" not in out and "doc 3" not in out
    assert "doc 2" in list_documents(docs, doc_class=None, doc_type="cnh", limit=10)


def test_docs_get_writes_downloaded_bytes(docs: IndexStore, tmp_path: Path) -> None:
    svc = FakeService(
        messages__get=lambda **kw: full_message("m1", attachments=[
            attachment_part("1", "f1.pdf", "application/pdf", 10)]),
        messages__attachments__get=lambda **kw: {"data": b64(b"%PDF-bytes")},
    )
    target = tmp_path / "out.pdf"
    msg = download(docs, lambda: svc, 1, target)
    assert target.read_bytes() == b"%PDF-bytes" and "out.pdf" in msg


def test_docs_get_unknown_id(docs: IndexStore, tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        download(docs, lambda: FakeService(), 999, tmp_path / "x")
