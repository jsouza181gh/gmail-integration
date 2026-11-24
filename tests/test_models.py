import pytest

from src.db.models import Email, Run, RunEmail
from src.db.scope import email_key


def test_tables_created(session_factory):
    with session_factory.begin() as s:
        s.add(Run(query="q"))
        s.add(Email(id="a"))
        s.flush()
        s.add(RunEmail(run_id=1, email_id="a"))

    with session_factory() as s:
        run = s.get(Run, 1)
        assert run.query == "q"
        assert run.pages == 0
        assert run.fetch_done is False
        assert s.get(Email, email_key("a")).attempts == 0
        assert s.get(RunEmail, (1, "a")).added_at is not None


def test_get_database_url_requires_env(monkeypatch):
    from src.db.session import get_database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_database_url()


def test_vector_roundtrip_sqlite(session_factory):
    from src.db.models import EmailEmbedding
    from src.db.vector import EMBED_DIM

    vec = tuple(i / EMBED_DIM for i in range(EMBED_DIM))
    with session_factory.begin() as s:
        s.add(Email(id="v"))
        s.flush()
        s.add(EmailEmbedding(email_id="v", model="m", embedding=vec))
    with session_factory() as s:
        got = s.get_one(EmailEmbedding, email_key("v")).embedding
    assert len(got) == EMBED_DIM
    assert all(abs(a - b) < 1e-6 for a, b in zip(got, vec, strict=True))


def test_vector_rejects_wrong_dim(session_factory):
    from sqlalchemy.exc import StatementError

    from src.db.models import EmailEmbedding

    with pytest.raises(StatementError, match="dimens"):
        with session_factory.begin() as s:
            s.add(Email(id="w"))
            s.flush()
            s.add(EmailEmbedding(email_id="w", model="m", embedding=(1.0, 2.0, 3.0)))


def test_job_checks_for_new_kinds(session_factory):
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    from src.db.models import Job

    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            s.add(Job(kind="backfill_page", priority=0, status="queued", attempts=0,
                      max_attempts=5, run_after=now, idempotency_key="k", created_at=now))


def test_attachment_unique_per_part(session_factory):
    from sqlalchemy.exc import IntegrityError

    from src.db.models import Attachment

    def att() -> Attachment:
        return Attachment(email_id="x", part_id="1", filename="a.pdf", mime_type="application/pdf",
                          size=10, status="pending")

    with session_factory.begin() as s:
        s.add(Email(id="x"))
        s.flush()
        s.add(att())
    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            s.add(att())


def test_auth_tables_roundtrip(session_factory):
    from datetime import UTC, datetime, timedelta

    from src.db.models import AuthSession, RecoveryCode, User

    now = datetime.now(UTC)
    with session_factory.begin() as s:
        u = User(username="joao", password_hash="h", totp_secret_enc="e", created_at=now,
                 password_changed_at=now)
        s.add(u)
        s.flush()
        s.add_all([RecoveryCode(user_id=u.id, code_hash=f"c{i}") for i in range(2)])
        s.add(AuthSession(user_id=u.id, family_id="f1", token_hash="t1", created_at=now,
                          last_used_at=now, expires_at=now + timedelta(days=14), ip="1.2.3.4",
                          user_agent="ua"))
    with session_factory() as s:
        assert s.get_one(User, 1).username == "joao"
        assert s.get_one(AuthSession, 1).revoked_at is None


def test_ip_block_pk_unique(session_factory):
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    from src.db.models import IpBlock

    now = datetime.now(UTC)
    with session_factory.begin() as s:
        s.add(IpBlock(ip="1.2.3.4", reason="r", created_at=now))
    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            s.add(IpBlock(ip="1.2.3.4", reason="r", created_at=now))


def _chat_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _chat_turn(s):
    from src.db.models import ChatConversation, ChatMessage

    now = _chat_now()
    conv = ChatConversation(title="t", created_at=now, updated_at=now)
    s.add(conv)
    s.flush()
    user = ChatMessage(conversation_id=conv.id, role="user", text="oi", input="text",
                       status="done", created_at=now, updated_at=now)
    s.add(user)
    s.flush()
    bot = ChatMessage(conversation_id=conv.id, role="assistant", text="", input="text",
                      status="queued", reply_to=user.id, created_at=now, updated_at=now)
    s.add(bot)
    s.flush()
    return conv, user, bot


def test_chat_tables_roundtrip(session_factory):
    from src.db.models import ChatAudio, ChatCard, ChatCardItem, ChatMessage

    now = _chat_now()
    with session_factory.begin() as s:
        s.add(Email(id="e1"))
        _, user, bot = _chat_turn(s)
        s.add(ChatAudio(message_id=user.id, mime_type="audio/wav", data=b"RIFF", duration_ms=700,
                        created_at=now))
        card = ChatCard(message_id=bot.id, kind="rule", status="proposed", channel="audio",
                        rule_name="lixo", sender_domain="loja.com", action="trash",
                        unless_document=True, affected=3, created_at=now)
        s.add(card)
        s.flush()
        s.add_all([ChatCardItem(card_id=card.id, position=i, email_id="e1") for i in range(2)])
    with session_factory() as s:
        assert s.get_one(ChatMessage, 2).reply_to == 1
        assert s.get_one(ChatAudio, 1).data == b"RIFF"
        assert s.get_one(ChatCard, 1).apply_existing is False


def test_card_item_exactly_one_target(session_factory):
    from sqlalchemy.exc import IntegrityError

    from src.db.models import ChatCard, ChatCardItem

    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            s.add(Email(id="e1"))
            _, _, bot = _chat_turn(s)
            card = ChatCard(message_id=bot.id, kind="emails", status="info", channel="chat",
                            created_at=_chat_now())
            s.add(card)
            s.flush()
            s.add(ChatCardItem(card_id=card.id, position=0, email_id="e1", action_id=None,
                               attachment_id=None))
            s.flush()
            s.add(ChatCardItem(card_id=card.id, position=1))


def test_rule_card_requires_condition(session_factory):
    from sqlalchemy.exc import IntegrityError

    from src.db.models import ChatCard

    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            _, _, bot = _chat_turn(s)
            s.add(ChatCard(message_id=bot.id, kind="rule", status="proposed", channel="chat",
                           rule_name="x", action="trash", created_at=_chat_now()))


def test_job_chat_turn_requires_message(session_factory):
    from sqlalchemy.exc import IntegrityError

    from src.db.models import Job

    now = _chat_now()
    with pytest.raises(IntegrityError):
        with session_factory.begin() as s:
            s.add(Job(kind="chat_turn", priority=50, status="queued", attempts=0,
                      max_attempts=5, run_after=now, idempotency_key="k", created_at=now))


def test_create_config_copies_chat_prompt(session_factory):
    from src.agent.config import DbAgentConfigs
    from src.db.models import AgentConfig

    with session_factory.begin() as s:
        s.add(AgentConfig(version=1, active=True, decide_prompt="d", chat_prompt="c1", max_turns=4,
                          created_at=_chat_now()))
    configs = DbAgentConfigs(session_factory)
    assert configs.create("d2", 4).chat_prompt == "c1"
    assert configs.create("d3", 4, chat_prompt="c2").chat_prompt == "c2"
