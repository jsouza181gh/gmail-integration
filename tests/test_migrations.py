from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

TABLES = {"runs", "emails", "run_emails", "watch_state"}


def test_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 't.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")

    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(create_engine(url)).get_table_names())

    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}
