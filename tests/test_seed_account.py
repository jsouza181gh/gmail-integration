from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    AgentConfig,
    Category,
    CategoryExample,
    CategoryFlag,
    CategorySet,
    GmailAccount,
    KbEntry,
    ToolPolicy,
)
from src.db.scope import account_scope, system_scope
from src.db.seed_account import generic_source_account, seed_generic_config
from tests.conftest import add_account


def _fill(sf: sessionmaker[Session], account: int) -> None:
    with account_scope(account), sf.begin() as s:
        old = CategorySet(version=1, active=False)
        live = CategorySet(version=2, active=True)
        s.add_all([old, live])
        s.flush()
        s.add(CategoryFlag(set_id=live.id, name="urgente", definition="d"))
        cat = Category(set_id=live.id, name="Trabalho", definition="d", color="blue", position=1)
        s.add(cat)
        s.add(Category(set_id=old.id, name="Antiga", definition="d", position=1))
        s.flush()
        s.add(CategoryExample(category_id=cat.id, position=1, text="reunião"))
        s.add(AgentConfig(version=3, active=True, decide_prompt="D", chat_prompt="C", max_turns=4))
        s.add(AgentConfig(version=2, active=False, decide_prompt="old", chat_prompt="old", max_turns=4))
        s.add(ToolPolicy(target="apply_labels", trigger="webhook", policy="auto"))
        s.add(KbEntry(kind="preference", scope="all", text="segredo do João", source="manual"))


def test_copies_only_the_generic_active_config(session_factory: sessionmaker[Session]) -> None:
    add_account(session_factory, 2, "ana@example.com")
    _fill(session_factory, 1)
    seed_generic_config(session_factory, source=1, target=2)
    with account_scope(2), session_factory() as s:
        sets = list(s.scalars(select(CategorySet)))
        assert [(c.version, c.active) for c in sets] == [(2, True)]
        assert [c.name for c in s.scalars(select(Category))] == ["Trabalho"]
        assert [f.name for f in s.scalars(select(CategoryFlag))] == ["urgente"]
        assert [e.text for e in s.scalars(select(CategoryExample))] == ["reunião"]
        cats = {c.id: c.set_id for c in s.scalars(select(Category))}
        assert set(cats.values()) == {sets[0].id}  # re-pointed at the new set
        configs = list(s.scalars(select(AgentConfig)))
        assert [(c.version, c.active, c.decide_prompt) for c in configs] == [(3, True, "D")]
        assert [(p.target, p.policy) for p in s.scalars(select(ToolPolicy))] == [("apply_labels", "auto")]
        assert list(s.scalars(select(KbEntry))) == []  # personal data stays behind


def test_source_is_the_template_account_else_the_first_admin(
    session_factory: sessionmaker[Session],
) -> None:
    assert generic_source_account(session_factory) is None
    from src.db.models import User

    now = datetime(2026, 11, 24, tzinfo=UTC)
    with session_factory.begin() as s:
        s.add(User(id=1, username="joao", role="admin", created_at=now, password_changed_at=now))
    assert generic_source_account(session_factory) == 1  # the admin's account
    with system_scope(), session_factory.begin() as s:
        s.add(GmailAccount(id=9, user_id=0, email_address="template@invalid", status="template",
                           connected_at=now))
    assert generic_source_account(session_factory) == 9  # a template wins
