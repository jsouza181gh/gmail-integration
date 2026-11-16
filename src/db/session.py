import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL não definida "
            "(ex.: postgresql+psycopg://user:senha@host:5432/db)."
        )
    return url


def make_session_factory(url: str | None = None) -> sessionmaker[Session]:
    url = url or get_database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite") and ":memory:" in url:
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    return sessionmaker(engine, expire_on_commit=False)
