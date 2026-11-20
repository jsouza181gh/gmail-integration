import base64

from src.agent.types import MailQuery
from src.gmail.reader import BODY_MAX_CHARS, GmailReader
from tests.fakes import FakeService, http_error


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(*parts: tuple[str, str]) -> dict[str, object]:
    return {
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {"size": 0},
            "parts": [{"mimeType": mime, "body": {"data": _b64(body)}} for mime, body in parts],
        },
    }


def _reader(svc: FakeService) -> GmailReader:
    return GmailReader(lambda: svc)


def test_reader_prefers_text_plain() -> None:
    svc = FakeService(
        messages__get=lambda **kw: _message(("text/html", "<p>html</p>"), ("text/plain", "plano"))
    )
    assert _reader(svc).get_body("m1") == "plano"
    assert svc.calls[0][1]["format"] == "full"


def test_reader_html_body_to_text() -> None:
    html = "<html><style>p{}</style><body><p>Olá&nbsp;mundo</p><script>x()</script>" \
        "<div>Prazo: 29/11</div></body></html>"
    svc = FakeService(messages__get=lambda **kw: _message(("text/html", html)))
    assert _reader(svc).get_body("m1") == "Olá mundo\nPrazo: 29/11"


def test_reader_nested_parts_and_truncation() -> None:
    long = "x" * (BODY_MAX_CHARS + 50)
    nested = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64(long)}},
                ]},
                {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "z"}},
            ],
        },
    }
    svc = FakeService(messages__get=lambda **kw: nested)
    body = _reader(svc).get_body("m1")
    assert len(body) == BODY_MAX_CHARS and set(body) == {"x"}


def test_reader_single_part_body() -> None:
    msg = {"id": "m1", "payload": {"mimeType": "text/plain", "body": {"data": _b64("só isso")}}}
    svc = FakeService(messages__get=lambda **kw: msg)
    assert _reader(svc).get_body("m1") == "só isso"


def test_reader_lists_messages_with_metadata() -> None:
    def get(**kw: object) -> dict[str, object]:
        return {
            "id": kw["id"],
            "threadId": "t",
            "labelIds": ["INBOX", "UNREAD"],
            "snippet": "oi",
            "payload": {"headers": [
                {"name": "From", "value": "Ana <ana@x.com>"},
                {"name": "Subject", "value": "Olá"},
                {"name": "Date", "value": "Thu, 19 Nov 2026 10:00:00 +0000"},
            ]},
        }

    svc = FakeService(
        messages__list=lambda **kw: {
            "messages": [{"id": "m1"}], "nextPageToken": "p2", "resultSizeEstimate": 7
        },
        messages__get=get,
    )
    page = _reader(svc).list_messages(MailQuery(query="is:unread", max_results=5))
    assert page.next_page_token == "p2" and page.result_size_estimate == 7
    (item,) = page.items
    assert (item.sender, item.subject, item.labels) == ("Ana <ana@x.com>", "Olá", ("INBOX", "UNREAD"))
    list_kw = svc.calls[0][1]
    assert list_kw["q"] == "is:unread" and list_kw["maxResults"] == 5


def test_reader_defaults_to_inbox_without_filters() -> None:
    svc = FakeService(messages__list=lambda **kw: {})
    _reader(svc).list_messages(MailQuery())
    assert svc.calls[0][1]["labelIds"] == ["INBOX"]


def test_reader_lists_labels() -> None:
    svc = FakeService(labels__list=lambda **kw: {"labels": [{"id": "L1", "name": "IA/Promoções", "type": "user"}]})
    (label,) = _reader(svc).list_labels()
    assert (label.id, label.name, label.type) == ("L1", "IA/Promoções", "user")


def test_item_404_returns_none() -> None:
    def get(**kw: object) -> dict[str, object]:
        raise http_error(404)

    assert _reader(FakeService(messages__get=get)).item("gone") is None
