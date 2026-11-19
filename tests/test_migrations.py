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
    "jobs",
}
EMAIL_COLUMNS = {
    "received_at", "is_relevant", "deadline", "has_document", "ephemeral", "category_set_id",
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
