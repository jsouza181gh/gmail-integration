from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.classification.seed import V1
from src.db.models import (
    AgentConfig,
    AgentRun,
    Attachment,
    Email,
    Feedback,
    Job,
    KbEntry,
    SuggestedAction,
)
from src.db.store import Store
from tests.api_fakes import Api

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def seed(sf: sessionmaker[Session]) -> None:
    Store(sf).create_category_set(V1)
    with sf.begin() as s:
        for i, (cat, rel, dl) in enumerate([
            ("Estudos", True, date(2026, 11, 20)),
            ("Promoções", False, None),
            ("Finanças", True, None),
            ("Carreira", True, date(2026, 12, 30)),
            ("Promoções", False, None),
        ]):
            s.add(Email(id=f"e{i}", sender=f"Loja <s{i}@loja.com>", subject=f"assunto {i}",
                        snippet=f"snip {i}", category=cat, is_relevant=rel, deadline=dl,
                        received_at=T0 - timedelta(hours=i), attempts=0, reason=f"motivo {i}",
                        body=f"corpo {i}"))
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", max_turns=4))
        s.flush()
        s.add(Attachment(email_id="e1", part_id="1", filename="rg.pdf", mime_type="application/pdf",
                         size=10, status="done", doc_class="pessoal", doc_type="rg", title="RG"))
        s.add(AgentRun(id=1, trigger="webhook", email_id="e1", config_id=1, status="done",
                       summary="ok", started_at=T0))
        s.add(SuggestedAction(email_id="e1", kind="trash", reason="promo", status="pending",
                              origin="agent", run_id=1, created_at=T0))
        s.add(SuggestedAction(email_id="e4", kind="archive", reason="promo", status="pending",
                              origin="agent", created_at=T0))


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    seed(session_factory)
    a = Api(session_factory)
    a.login()
    return a


def test_requires_login(session_factory: sessionmaker[Session]) -> None:
    seed(session_factory)
    assert Api(session_factory).client.get("/api/emails").status_code == 401


def test_list_filters_and_keyset_page(api: Api) -> None:
    c = api.client
    page = c.get("/api/emails", params={"limit": 2}).json()
    assert [e["id"] for e in page["items"]] == ["e0", "e1"]
    nxt = c.get("/api/emails", params={"limit": 2, "before": page["next_before"]}).json()
    assert [e["id"] for e in nxt["items"]] == ["e2", "e3"]
    promos = c.get("/api/emails", params={"category": "Promoções"}).json()["items"]
    assert [e["id"] for e in promos] == ["e1", "e4"] and promos[0]["pending_actions"] == 1
    assert promos[0]["attachments"] == 1
    assert [e["id"] for e in c.get("/api/emails", params={"q": "snip 3"}).json()["items"]] == ["e3"]
    assert {e["id"] for e in c.get("/api/emails", params={"relevant": "true"}).json()["items"]} == {"e0", "e2", "e3"}


def test_relevant_ordered_by_deadline(api: Api) -> None:
    items = api.client.get("/api/emails/relevant").json()
    assert [e["id"] for e in items] == ["e0", "e3", "e2"]
    assert items[0]["due_soon"] is True and items[1]["due_soon"] is False


def test_detail_with_attachments_actions_runs(api: Api) -> None:
    d = api.client.get("/api/emails/e1").json()
    assert d["item"]["id"] == "e1" and d["body"] == "corpo 1" and d["reason"] == "motivo 1"
    assert [a["title"] for a in d["attachments"]] == ["RG"]
    assert [a["kind"] for a in d["actions"]] == ["trash"]
    assert [r["id"] for r in d["runs"]] == [1]
    assert api.client.get("/api/emails/nope").status_code == 404


def test_reclassify(api: Api) -> None:
    c = api.client
    r = c.post("/api/emails/e1/reclassify", json={"category": "Compras", "comment": "é pedido meu"})
    assert r.status_code == 204
    assert c.post("/api/emails/e1/reclassify", json={"category": "Inventada"}).status_code == 422
    with api.sf() as s:
        assert s.get_one(Email, "e1").category == "Compras"
        fb = s.scalars(select(Feedback)).one()
        kb = s.scalars(select(KbEntry).where(KbEntry.scope == "classification")).one()
    assert (fb.verdict, fb.source, fb.email_id) == ("reclassify", "ui", "e1")
    assert "s1@loja.com" in kb.text and "Compras" in kb.text and "é pedido meu" in kb.text
    assert "assunto 1" not in kb.text  # never third-party text in the prompt


def test_actions_list_flags_important_attachment(api: Api) -> None:
    acts = api.client.get("/api/actions").json()
    by_email = {a["email_id"]: a for a in acts}
    assert by_email["e1"]["has_important_attachment"] is True
    assert by_email["e4"]["has_important_attachment"] is False
    assert by_email["e1"]["subject"] == "assunto 1"


def test_approve_enqueues_and_reject_writes_feedback(api: Api) -> None:
    acts = {a["email_id"]: a["id"] for a in api.client.get("/api/actions").json()}
    r = api.client.post("/api/actions/approve", json={"ids": [acts["e1"]]})
    assert r.json() == {"decided": 1}
    r = api.client.post("/api/actions/reject", json={"ids": [acts["e4"]], "comment": "quero ver"})
    assert r.json() == {"decided": 1}
    assert api.client.post("/api/actions/approve", json={"ids": [acts["e1"]]}).json() == {"decided": 0}
    with api.sf() as s:
        kinds = list(s.scalars(select(Job.kind)))
        sources = set(s.scalars(select(Feedback.source)))
    assert kinds == ["execute_action"] and sources == {"ui"}


def test_categories(api: Api) -> None:
    cats = api.client.get("/api/categories").json()
    assert len(cats["categories"]) == 11 and len(cats["flags"]) == 4
