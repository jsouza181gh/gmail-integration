import pytest

from src.gmail.full import fetch_attachment, fetch_full, parse_full
from tests.fakes import FakeService, http_error
from tests.gmail_samples import attachment_part, b64, full_message


def test_parse_full_body_and_attachments() -> None:
    raw = full_message("m1", body="Olá, segue o boleto", attachments=[
        attachment_part("1", "boleto.pdf", "application/pdf", 50_000),
        attachment_part("2", "logo.png", "image/png", 4_000, inline_cid=True),
    ])
    full = parse_full(raw)
    assert full.summary.id == "m1" and full.summary.subject == "assunto m1"
    assert full.summary.received_at is not None and not full.summary.system
    assert full.body == "Olá, segue o boleto"
    assert [(a.part_id, a.filename, a.mime_type, a.size, a.inline) for a in full.attachments] == [
        ("1", "boleto.pdf", "application/pdf", 50_000, False),
        ("2", "logo.png", "image/png", 4_000, True),
    ]


def test_parse_full_flags_system_mail() -> None:
    assert parse_full(full_message("m1", system=True)).summary.system


def test_fetch_full_gone_is_none() -> None:
    def get(**kw: object) -> object:
        raise http_error(404)

    assert fetch_full(FakeService(messages__get=get), "m1") is None


def test_fetch_attachment_resolves_part_id() -> None:
    gets = iter(range(100))

    def get(**kw: object) -> object:
        # Gmail hands out a different attachmentId on every read
        return full_message("m1", attachments=[
            attachment_part("1", "a.pdf", "application/pdf", 10, att_id=f"v{next(gets)}"),
        ])

    def att(**kw: object) -> object:
        assert kw["id"] == "v0-1" and kw["messageId"] == "m1"
        return {"data": b64(b"%PDF-1.4 bytes"), "size": 14}

    svc = FakeService(messages__get=get, messages__attachments__get=att)
    assert fetch_attachment(svc, "m1", "1") == b"%PDF-1.4 bytes"


def test_fetch_attachment_missing_part_raises() -> None:
    svc = FakeService(messages__get=lambda **kw: full_message("m1"))
    with pytest.raises(LookupError):
        fetch_attachment(svc, "m1", "9")
