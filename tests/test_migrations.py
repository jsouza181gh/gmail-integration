from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from src.db.models import Base

TABLES = {
    "runs", "emails", "run_emails", "watch_state",
    "category_sets", "category_flags", "categories", "category_examples",
    "kb_entries", "llm_calls", "llm_call_messages", "llm_call_emails",
    "jobs", "agent_configs", "agent_runs", "agent_steps",
    "tool_policies", "automation_rules", "suggested_actions", "gmail_labels", "feedback", "notifications",
    "email_embeddings", "attachments",
    "users", "recovery_codes", "auth_sessions", "login_failures", "ip_blocks",
    "security_events", "app_settings", "config_audit",
    "chat_conversations", "chat_messages", "chat_audio", "chat_cards", "chat_card_items",
    "chat_message_refs",
}
EMAIL_COLUMNS = {
    "received_at", "is_relevant", "deadline", "has_document", "ephemeral", "category_set_id",
    "body", "indexed_at", "index_error", "source",
}


def _upgraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, str]:
    url = f"sqlite:///{tmp_path / 't.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return cfg, url


def test_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, url = _upgraded(tmp_path, monkeypatch)
    insp = inspect(create_engine(url))
    assert TABLES <= set(insp.get_table_names())
    assert EMAIL_COLUMNS <= {c["name"] for c in insp.get_columns("emails")}

    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}


def test_seed_v1_taxonomy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as c:
        assert c.execute(text("select count(*) from categories")).scalar() == 11
        assert c.execute(text("select count(*) from category_flags")).scalar() == 4
        assert (c.execute(text("select count(*) from category_examples")).scalar() or 0) >= 11
        assert c.execute(text("select count(*) from category_sets where active")).scalar() == 1
        assert c.execute(text("select count(*) from kb_entries")).scalar() == 1


def test_no_json_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    insp = inspect(create_engine(url))
    json_cols = [
        (t, c["name"])
        for t in insp.get_table_names()
        for c in insp.get_columns(t)
        if "JSON" in str(c["type"]).upper()
    ]
    assert json_cols == []


def test_models_match_migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_seed_tool_policies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as c:
        rows = set(c.execute(text("select target, trigger, policy from tool_policies")).all())
    assert ("apply_labels", "webhook", "auto") in rows and len(rows) == 12
    assert {p for t, _, p in rows if t != "apply_labels"} == {"suggest"}


def test_seed_agent_config_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as c:
        rows = c.execute(
            text("select version, active, max_turns from agent_configs order by version")
        ).all()
    assert [tuple(r) for r in rows] == [(1, False, 4), (2, False, 4), (3, True, 4)]


def test_chat_columns_and_seeded_chat_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, url = _upgraded(tmp_path, monkeypatch)
    insp = inspect(create_engine(url))
    assert "chat_message_id" in {c["name"] for c in insp.get_columns("jobs")}
    assert "unless_document" in {c["name"] for c in insp.get_columns("automation_rules")}
    with create_engine(url).connect() as c:
        prompts = c.execute(text("select chat_prompt from agent_configs")).scalars().all()
    assert prompts and all(p and "propose_" in p for p in prompts)


def test_migration_0015_upgrade_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, url = _upgraded(tmp_path, monkeypatch)
    command.downgrade(cfg, "0014")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("insert into emails (id, attempts, updated_at) values ('old', 0, '2026-11-20')"))
    command.upgrade(cfg, "0015")
    insp = inspect(create_engine(url))
    assert "chat_message_refs" in insp.get_table_names()
    with create_engine(url).connect() as c:
        assert c.execute(text("select source from emails where id = 'old'")).scalar() == "inbox"
    command.downgrade(cfg, "0014")
    insp = inspect(create_engine(url))
    assert "chat_message_refs" not in insp.get_table_names()
    assert "source" not in {c["name"] for c in insp.get_columns("emails")}


def test_migration_0016_activates_v2_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.agent.prompts import CHAT_PROMPT_V2

    cfg, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as c:
        active = c.execute(text("select version, chat_prompt, decide_prompt from agent_configs"
                                " where active")).one()
        v1_decide = c.execute(text("select decide_prompt from agent_configs where version = 1")).scalar()
    assert active[0] == 3 and active[2] == v1_decide
    command.downgrade(cfg, "0019")
    with create_engine(url).connect() as c:
        v2 = c.execute(text("select version, chat_prompt from agent_configs where active")).one()
    assert v2[0] == 2 and v2[1] == CHAT_PROMPT_V2
    command.downgrade(cfg, "0015")
    with create_engine(url).connect() as c:
        rows = c.execute(text("select version, active from agent_configs")).all()
    assert [tuple(r) for r in rows] == [(1, True)]


def test_migration_0018_backfill_attachment_constraint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    cfg, url = _upgraded(tmp_path, monkeypatch)
    eng = create_engine(url)
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(text(
            "insert into jobs (kind, priority, status, attempts, max_attempts, run_after,"
            " idempotency_key, created_at) values ('backfill_attachment', 1, 'queued', 0, 5,"
            " '2026-11-21', 'k', '2026-11-21')"
        ))
    command.downgrade(cfg, "0017")


def test_migration_0020_activates_chat_prompt_v3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agent.prompts import CHAT_PROMPT_V3

    _, url = _upgraded(tmp_path, monkeypatch)
    with create_engine(url).connect() as c:
        prompt = c.execute(text("select chat_prompt from agent_configs where active")).scalar()
    assert prompt == CHAT_PROMPT_V3
