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

import httpx
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from landed.config import settings
from landed.core import providers
from landed.db.models import Base, Project, User

TEST_DATABASE = "landed_test"
UNREACHABLE = (
    "PostgreSQL is not reachable at {url}. Start it with `docker compose up -d`."
)


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the internet unless it asks to.

    A developer with a working key in .env would otherwise have provider tests
    quietly making paid calls — slow, non-deterministic, and billed. Mark a test
    `@pytest.mark.live` to opt in.
    """
    if request.node.get_closest_marker("live"):
        return

    def refuse(*_args, **_kwargs):
        raise RuntimeError(
            "network call attempted in a test; use a stub or mark it @pytest.mark.live"
        )

    for verb in ("get", "post", "request"):
        monkeypatch.setattr(httpx, verb, refuse)


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuration with no API keys, regardless of what is in .env.

    Built with `model_copy` rather than `Settings(gemini_api_key=None)`: pydantic
    -settings treats a None passed to the initialiser as "not provided" and falls
    straight back to the environment.
    """
    blank = settings().model_copy(
        update={"anthropic_api_key": None, "gemini_api_key": None}
    )
    monkeypatch.setattr(providers, "settings", lambda: blank)


def _test_database_url() -> URL:
    """The configured server, pointed at a separate test database.

    Returned as a `URL` rather than a string on purpose: `str(URL)` masks the
    password as `***`, and that literal would then be sent as the password.
    """
    return make_url(settings().database_url).set(database=TEST_DATABASE)


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
