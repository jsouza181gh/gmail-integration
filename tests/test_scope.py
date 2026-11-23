"""Account scope: every ORM read/write on account-owned tables is confined to
the current account, and touching them with no scope fails closed."""
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import Integer, String, delete, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from src.db.models import Base, GmailAccount, User
from src.db.scope import AccountOwned, ScopeError, account_scope, current_account, system_scope
from src.db.session import make_session_factory

pytestmark = pytest.mark.noscope


class _Note(AccountOwned, Base):
    __tablename__ = "test_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(32), nullable=False)


@pytest.fixture
def sf() -> sessionmaker[Session]:
    sf = make_session_factory("sqlite:///:memory:")
    Base.metadata.create_all(sf.kw["bind"])
    now = datetime.now(UTC)
    with system_scope(), sf.begin() as s:
        for i in (1, 2):
            s.add(GmailAccount(id=i, user_id=i, email_address=f"u{i}@example.com",
                               status="connected", connected_at=now))
    with account_scope(1), sf.begin() as s:
        s.add(_Note(text="a"))
    with account_scope(2), sf.begin() as s:
        s.add(_Note(text="b"))
    return sf


def _texts(sf: sessionmaker[Session]) -> list[str]:
    with sf() as s:
        return sorted(s.scalars(select(_Note.text)))


def test_reads_only_current_account(sf: sessionmaker[Session]) -> None:
    with account_scope(1):
        assert _texts(sf) == ["a"]
    with account_scope(2):
        assert _texts(sf) == ["b"]


def test_insert_fills_account(sf: sessionmaker[Session]) -> None:
    with account_scope(2), sf.begin() as s:
        note = _Note(text="c")
        s.add(note)
        s.flush()
        assert note.gmail_account_id == 2
        assert current_account() == 2


def test_get_by_pk_respects_scope(sf: sessionmaker[Session]) -> None:
    with system_scope(), sf() as s:
        b_id = s.scalars(select(_Note.id).where(_Note.text == "b")).one()
    with account_scope(1), sf() as s:
        assert s.get(_Note, b_id) is None


def test_update_delete_respect_scope(sf: sessionmaker[Session]) -> None:
    with account_scope(1), sf.begin() as s:
        s.execute(update(_Note).values(text="z"))
    with account_scope(1), sf.begin() as s:
        s.execute(delete(_Note))
    with system_scope():
        assert _texts(sf) == ["b"]


def test_no_scope_raises(sf: sessionmaker[Session]) -> None:
    with pytest.raises(ScopeError):
        _texts(sf)
    with pytest.raises(ScopeError):
        current_account()


def test_insert_without_account_raises(sf: sessionmaker[Session]) -> None:
    with pytest.raises(ScopeError), system_scope(), sf.begin() as s:
        s.add(_Note(text="x"))


def test_system_scope_sees_all(sf: sessionmaker[Session]) -> None:
    with system_scope():
        assert _texts(sf) == ["a", "b"]


def test_thread_without_scope_fails_closed(sf: sessionmaker[Session]) -> None:
    errors: list[BaseException] = []

    def work() -> None:
        try:
            _texts(sf)
        except BaseException as exc:  # noqa: BLE001 - captured for the assert
            errors.append(exc)

    with account_scope(1):
        t = threading.Thread(target=work)
        t.start()
        t.join()
    assert len(errors) == 1 and isinstance(errors[0], ScopeError)


def test_non_owned_tables_need_no_scope(sf: sessionmaker[Session]) -> None:
    with sf() as s:
        assert s.scalars(select(User)).all() == []


OWNED_TABLES = (
    "runs", "run_emails", "emails", "email_embeddings", "attachments", "category_sets",
    "category_flags", "categories", "category_examples", "kb_entries", "agent_configs",
    "agent_runs", "agent_steps", "gmail_labels", "tool_policies", "automation_rules",
    "suggested_actions", "feedback", "notifications", "jobs", "llm_calls",
    "llm_call_messages", "llm_call_emails", "config_audit", "chat_conversations",
    "chat_messages", "chat_message_refs", "chat_audio", "chat_cards", "chat_card_items",
    "account_settings",
)


def test_models_owned_tables() -> None:
    missing = [t for t in OWNED_TABLES if "gmail_account_id" not in Base.metadata.tables[t].c]
    assert missing == []


def test_unique_version_per_account(sf: sessionmaker[Session]) -> None:
    from src.db.models import CategorySet

    for account in (1, 2):
        with account_scope(account), sf.begin() as s:
            s.add(CategorySet(version=1, active=True))
    with system_scope(), sf() as s:
        assert len(s.scalars(select(CategorySet)).all()) == 2
