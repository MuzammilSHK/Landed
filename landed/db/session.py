"""Engine and session management.

The same code path serves PostgreSQL and SQLite. SQLite needs two nudges to behave:
`check_same_thread=False` because the web server hands connections between threads,
and `PRAGMA foreign_keys=ON` because SQLite ignores foreign keys unless asked, which
would silently defeat every `ondelete="CASCADE"` in the models.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from landed.config import settings
from landed.db.models import Base


def build_engine(url: str | None = None) -> Engine:
    """Create an engine for `url`, defaulting to the configured database.

    In-memory SQLite additionally needs `StaticPool`: without it every connection
    gets its own empty database, so a schema created on one is invisible to the next.
    """
    resolved = url or settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if resolved.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_in_memory(resolved):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(resolved, **kwargs)
    if resolved.startswith("sqlite"):
        _enforce_sqlite_foreign_keys(engine)
    return engine


def _is_in_memory(url: str) -> bool:
    return url in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in url


def _enforce_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine(), expire_on_commit=False)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency. One session per request, always closed."""
    with session_factory()() as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and tests. Commits on success, rolls back on error."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(target: Engine | None = None) -> None:
    """Create the schema directly.

    For tests and first-run convenience. Alembic owns schema changes everywhere else.
    """
    Base.metadata.create_all(target or engine())
