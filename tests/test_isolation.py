"""Two accounts in one database: user 1 never sees anything of account 2,
through any panel route or chat tool (spec: "cada usuário vê só os próprios
dados")."""
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.auth.crypto import issue_download
from src.chat.retrieval import DbEmailLookup, DbRetriever
from src.classification.seed import V1
from src.db.models import (
    AgentConfig,
    AgentRun,
    Attachment,
    AutomationRule,
    ChatCard,
    ChatConversation,
    ChatMessage,
    ConfigAudit,
    Email,
    Job,
    KbEntry,
    LlmCall,
    Notification,
    Run,
    SuggestedAction,
)
from src.db.scope import account_scope
from src.db.store import Store
from src.indexing.store import IndexStore
from src.llm.types import CallContext
from tests.api_fakes import SETTINGS, Api

pytestmark = pytest.mark.noscope

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
SECRET = "SEGREDO2"


class Ids:
    conversation = 0
    message = 0
    card = 0
    action = 0
    rule = 0
    kb = 0
    run = 0
    attachment = 0
    job = 0
    config = 0


def _seed_other(sf: sessionmaker[Session], account: int) -> Ids:
    ids = Ids()
    with account_scope(account), sf.begin() as s:
        s.add(Email(id="x1", sender=f"{SECRET} <a@b.c>", subject=SECRET, snippet=SECRET,
                    body=SECRET, category="Finanças", is_relevant=True, received_at=T0, attempts=0,
                    reason=SECRET))
        cfg = AgentConfig(version=1, active=True, decide_prompt=SECRET, chat_prompt=SECRET,
                          max_turns=4)
        s.add(cfg)
        s.flush()
        att = Attachment(email_id="x1", part_id="1", filename=f"{SECRET}.pdf",
                         mime_type="application/pdf", size=1, status="done", doc_class="financeiro",
                         doc_type="boleto", title=SECRET, summary=SECRET)
        run = AgentRun(trigger="webhook", email_id="x1", config_id=cfg.id, status="done",
                       summary=SECRET, started_at=T0)
        rule = AutomationRule(name=SECRET, sender_domain="b.c", action="archive", mode="suggest",
                              active=True, fired_count=0, approved_count=0, rejected_count=0,
                              source="ui", created_at=T0)
        kb = KbEntry(kind="preference", scope="classification", text=SECRET, source="ui")
        conv = ChatConversation(title=SECRET, created_at=T0, updated_at=T0)
        backfill = Run(query=SECRET)
        s.add_all([att, run, rule, kb, conv, backfill])
        s.flush()
        action = SuggestedAction(email_id="x1", kind="archive", reason=SECRET, status="pending",
                                 origin="agent", run_id=run.id, created_at=T0)
        msg = ChatMessage(conversation_id=conv.id, role="assistant", text=SECRET, input="text",
                          status="done", created_at=T0, updated_at=T0)
        job = Job(kind="process_email", priority=0, status="dead", attempts=5, max_attempts=5,
                  run_after=T0, idempotency_key="process_email:x1", email_id="x1",
                  last_error=SECRET, created_at=T0)
        s.add_all([action, msg, job])
        s.flush()
        card = ChatCard(message_id=msg.id, kind="actions", status="pending", channel="chat",
                        rule_name=SECRET, created_at=T0)
        s.add(card)
        s.add(Notification(kind="job_dead", subject=SECRET, body=SECRET, status="sent",
                           created_at=T0))
        s.add(ConfigAudit(entity="rule", entity_id=str(rule.id), channel="ui", before=None,
                          after=SECRET, created_at=T0))
        s.add(LlmCall(purpose=SECRET, model=SECRET, attempt=1, status="ok", latency_ms=1,
                      input_tokens=1, output_tokens=1, total_tokens=2, cached_input_tokens=0,
                      reasoning_tokens=0, audio_input_tokens=0))
        s.flush()
        ids.conversation, ids.message, ids.card = conv.id, msg.id, card.id
        ids.action, ids.rule, ids.kb, ids.run = action.id, rule.id, kb.id, run.id
        ids.attachment, ids.job, ids.config = att.id, job.id, cfg.id
    return ids


@pytest.fixture
def world(session_factory: sessionmaker[Session], second_account: int) -> tuple[Api, Ids]:
    ids = _seed_other(session_factory, second_account)
    with account_scope(1):  # account 1 is a normal, working account
        Store(session_factory).create_category_set(V1)
    api = Api(session_factory)
    api.login()
    return api, ids


LISTS = (
    "/api/emails", "/api/emails/relevant", "/api/categories", "/api/actions",
    "/api/documents", "/api/agent/config", "/api/agent/policies", "/api/agent/rules",
    "/api/agent/kb", "/api/agent/settings", "/api/agent/audit", "/api/chat/conversations",
    "/api/backfill", "/api/runs", "/api/jobs", "/api/usage", "/api/notifications",
)


@pytest.mark.parametrize("path", LISTS)
def test_lists_show_only_own_data(world: tuple[Api, Ids], path: str) -> None:
    api, _ = world
    r = api.client.get(path)
    assert r.status_code == 200, r.text
    assert SECRET not in r.text


def _details(api: Api, ids: Ids) -> list[tuple[str, str, dict[str, object] | None]]:
    # a genuinely signed link for account 2's attachment: the token is not the guard here
    token = issue_download(ids.attachment, SETTINGS, api.clock())
    return [
        ("GET", "/api/emails/x1", None),
        ("POST", "/api/emails/x1/reclassify", {"category": "Compras"}),
        ("GET", f"/api/documents/{ids.attachment}/download?t={token}", None),
        ("POST", f"/api/documents/{ids.attachment}/link", None),
        ("POST", "/api/actions/approve", {"ids": [ids.action]}),
        ("POST", "/api/actions/reject", {"ids": [ids.action]}),
        ("POST", f"/api/agent/config/{ids.config}/activate", None),
        ("POST", f"/api/agent/rules/{ids.rule}/promote", None),
        ("POST", f"/api/agent/rules/{ids.rule}/disable", None),
        ("DELETE", f"/api/agent/kb/{ids.kb}", None),
        ("GET", f"/api/chat/conversations/{ids.conversation}/messages", None),
        ("POST", f"/api/chat/conversations/{ids.conversation}/messages", {"text": "oi"}),
        ("GET", f"/api/chat/messages/{ids.message}/audio", None),
        ("POST", f"/api/chat/messages/{ids.message}/retry", None),
        ("GET", f"/api/chat/messages/{ids.message}/stream", None),
        ("POST", f"/api/chat/cards/{ids.card}/confirm", {}),
        ("POST", f"/api/chat/cards/{ids.card}/cancel", None),
        ("GET", f"/api/runs/{ids.run}", None),
        ("POST", f"/api/jobs/{ids.job}/requeue", None),
    ]


def test_detail_of_other_account_is_404(
    world: tuple[Api, Ids], session_factory: sessionmaker[Session], second_account: int
) -> None:
    api, ids = world
    for method, path, body in _details(api, ids):
        r = api.client.request(method, path, json=body)
        assert r.status_code == 404 or (
            path.startswith("/api/actions/") and r.status_code == 200 and r.json() == {"decided": 0}
        ), f"{method} {path} -> {r.status_code} {r.text[:200]}"
        assert SECRET not in r.text, f"{method} {path}"
    with account_scope(second_account), session_factory() as s:  # nothing of 2 was touched
        assert s.get_one(SuggestedAction, ids.action).status == "pending"
        assert s.get_one(AutomationRule, ids.rule).active
        assert s.get_one(Job, ids.job).status == "dead"
        assert s.get_one(ChatCard, ids.card).status == "pending"


def test_chat_tools_never_see_other_account(
    world: tuple[Api, Ids], session_factory: sessionmaker[Session]
) -> None:
    class NoMail:
        def fetch(self, email_id: str) -> None:
            raise AssertionError("never fetched")

    with account_scope(1):
        hits = DbRetriever(IndexStore(session_factory), embedder=None).search(  # type: ignore[arg-type]
            SECRET, 10, CallContext()
        )
        assert hits == ()
        assert DbEmailLookup(session_factory, NoMail()).email("x1") is None  # type: ignore[arg-type]


def test_mailbox_total_cache_is_per_account() -> None:
    from types import SimpleNamespace

    from src.api.routes.ops import _ProfileCache

    totals = {1: 100, 2: 200}

    def gmail() -> object:
        from src.db.scope import current_account

        n = totals[current_account()]
        execute = SimpleNamespace(execute=lambda num_retries: {"messagesTotal": n})
        return SimpleNamespace(users=lambda: SimpleNamespace(getProfile=lambda userId: execute))

    deps = SimpleNamespace(clock=lambda: T0, gmail=gmail)
    cache = _ProfileCache()
    with account_scope(1):
        assert cache.total(deps) == 100  # type: ignore[arg-type]
    with account_scope(2):
        assert cache.total(deps) == 200  # type: ignore[arg-type]
