"""Shared fixtures.

Database tests run against real PostgreSQL, because a suite that runs on a different
engine than production is not testing what ships. Isolation comes from wrapping each
test in a transaction that is rolled back afterwards, rather than recreating the
schema per test — the schema is built once for the session.

`join_transaction_mode="create_savepoint"` is what makes that work: a `session.commit()`
inside a test releases a savepoint rather than committing the outer transaction, so
code under test can commit normally and still leave nothing behind.

If PostgreSQL is unreachable the database tests skip with an explicit reason. The rest
of the suite — engine, conflicts, providers — needs no database and always runs.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from landed.config import settings
from landed.db.models import Base, Project, User

TEST_DATABASE = "landed_test"
UNREACHABLE = (
    "PostgreSQL is not reachable at {url}. Start it with `docker compose up -d`."
)


def _test_database_url() -> str:
    return str(make_url(settings().database_url).set(database=TEST_DATABASE))


def _create_test_database_if_absent() -> None:
    """Connect to the maintenance database and create ours if it does not exist."""
    admin_url = make_url(settings().database_url).set(database="postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    try:
        _create_test_database_if_absent()
    except OperationalError:
        pytest.skip(UNREACHABLE.format(url=settings().database_url), allow_module_level=True)

    target = create_engine(_test_database_url())
    with target.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.drop_all(target)
    Base.metadata.create_all(target)
    yield target
    target.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def user(db: Session) -> User:
    account = User(email="buyer@example.com", password_hash="argon2-placeholder")
    db.add(account)
    db.commit()
    return account


@pytest.fixture
def project(db: Session, user: User) -> Project:
    item = Project(user_id=user.id, name="Q3 Enclosure Sourcing", base_currency="USD")
    db.add(item)
    db.commit()
    return item
