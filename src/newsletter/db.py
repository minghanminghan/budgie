from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    is_file = is_sqlite and url not in ("sqlite://", "sqlite:///:memory:")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            if is_file:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@lru_cache
def sessionmaker_for(url: str) -> sessionmaker[Session]:
    """One engine per URL per process, shared by worker jobs and CLI commands."""
    return make_sessionmaker(make_engine(url))


def sqlite_path(url: str) -> str:
    return make_url(url).database or ""
