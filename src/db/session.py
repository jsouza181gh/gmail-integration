import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.scope import install_scoping


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
    if url.startswith("sqlite") and ":memory:" in url:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_engine(url, pool_pre_ping=True)
    sf = sessionmaker(engine, expire_on_commit=False)
    install_scoping(sf)
    return sf
