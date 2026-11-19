from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Attachment, Email, SecurityEvent
from tests.api_fakes import Api
from tests.fakes import FakeService, http_error
from tests.gmail_samples import attachment_part, b64, full_message


def seed(sf: sessionmaker[Session]) -> None:
    with sf.begin() as s:
        s.add(Email(id="m1", subject="Sua conta", received_at=datetime(2026, 11, 1, tzinfo=UTC), attempts=0))
        s.add(Email(id="gone", subject="apagado", attempts=0))
        s.flush()
        s.add(Attachment(id=1, email_id="m1", part_id="1", filename="boleto.pdf", mime_type="application/pdf",
                         size=10, status="done", doc_class="fiscal", doc_type="boleto", title="Boleto Light",
                         issuer="Light", amount_cents=18990, due_on=date(2026, 11, 28)))
        s.add(Attachment(id=2, email_id="m1", part_id="2", filename="cnh.jpg", mime_type="image/jpeg",
                         size=10, status="done", doc_class="pessoal", doc_type="cnh", title="CNH"))
        s.add(Attachment(id=3, email_id="gone", part_id="1", filename="x.pdf", mime_type="application/pdf",
                         size=10, status="done", doc_class="fiscal", doc_type="nfe", title="NF"))


def gmail() -> FakeService:
    def get(**kw: object) -> object:
        if kw["id"] == "gone":
            raise http_error(404)
        return full_message("m1", attachments=[attachment_part("1", "boleto.pdf", "application/pdf", 10)])

    return FakeService(messages__get=get, messages__attachments__get=lambda **kw: {"data": b64(b"%PDF-1.7 boleto")})


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    seed(session_factory)
    a = Api(session_factory, gmail=gmail)
    a.login()
    return a


def test_list_filters(api: Api) -> None:
    c = api.client
    fiscal = c.get("/api/documents", params={"class": "fiscal"}).json()
    assert [d["title"] for d in fiscal] == ["NF", "Boleto Light"]
    assert fiscal[1]["amount_cents"] == 18990 and fiscal[1]["email_subject"] == "Sua conta"
    assert [d["id"] for d in c.get("/api/documents", params={"type": "cnh"}).json()] == [2]
    assert [d["id"] for d in c.get("/api/documents", params={"q": "light"}).json()] == [1]


def test_link_then_download_records_event(api: Api) -> None:
    link = api.client.post("/api/documents/1/link").json()
    assert link["expires_in"] == 60 and link["url"].startswith("/api/documents/1/download?t=")
    r = api.client.get(link["url"])
    assert r.status_code == 200 and r.content == b"%PDF-1.7 boleto"
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment;" in r.headers["content-disposition"] and "boleto.pdf" in r.headers["content-disposition"]
    with api.sf() as s:
        ev = s.scalars(select(SecurityEvent).where(SecurityEvent.kind == "download")).one()
    assert "boleto.pdf" in ev.detail


def test_expired_or_foreign_token_403(api: Api) -> None:
    url = api.client.post("/api/documents/1/link").json()["url"]
    token = url.split("t=")[1]
    assert api.client.get(f"/api/documents/2/download?t={token}").status_code == 403
    assert api.client.get("/api/documents/1/download?t=garbage").status_code == 403
    api.clock.advance(seconds=61)
    api.client.post("/api/auth/refresh")  # keep the session alive; only the link expired
    api.client.headers["X-CSRF-Token"] = api.client.cookies["csrf"]
    assert api.client.get(url).status_code == 403


def test_download_requires_session(session_factory: sessionmaker[Session]) -> None:
    seed(session_factory)
    a = Api(session_factory, gmail=gmail)
    a.login()
    url = a.client.post("/api/documents/1/link").json()["url"]
    a.client.cookies.clear()
    assert a.client.get(url).status_code == 401


def test_gone_attachment_410(api: Api) -> None:
    url = api.client.post("/api/documents/3/link").json()["url"]
    assert api.client.get(url).status_code == 410


def test_unknown_document_404(api: Api) -> None:
    assert api.client.post("/api/documents/99/link").status_code == 404
