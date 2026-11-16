import pytest

from src.db.models import Email, Run, RunEmail, WatchState


def test_tables_created(session_factory):
    with session_factory.begin() as s:
        s.add(Run(query="q"))
        s.add(Email(id="a"))
        s.flush()
        s.add(RunEmail(run_id=1, email_id="a"))
        s.add(WatchState(id=1, email_address="x@y", history_id="10", expiration=1))

    with session_factory() as s:
        run = s.get(Run, 1)
        assert run.query == "q"
        assert run.pages == 0
        assert run.fetch_done is False
        assert s.get(Email, "a").attempts == 0
        assert s.get(RunEmail, (1, "a")).added_at is not None
        assert s.get(WatchState, 1).history_id == "10"


def test_get_database_url_requires_env(monkeypatch):
    from src.db.session import get_database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_database_url()
