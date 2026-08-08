"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from landed.config import settings
from landed.db.models import Base


def build_engine(url: str | None = None) -> Engine:
    """Create an engine for `url`, defaulting to the configured database.

    `pool_pre_ping` costs one round trip per checkout and saves the class of failure
    where a connection went stale while the app was idle.
    """
    return create_engine(url or settings().database_url, pool_pre_ping=True)


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
    """Transactional scope for scripts. Commits on success, rolls back on error."""
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
    """Create the schema directly. Used by tests; Alembic owns it everywhere else."""
    Base.metadata.create_all(target or engine())
