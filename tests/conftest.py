"""Shared fixtures.

Each test gets an isolated in-memory database built through `build_engine`, not
`create_engine` directly — so the `PRAGMA foreign_keys=ON` wiring is genuinely
exercised. A suite that silently skipped foreign-key enforcement would not catch a
broken cascade.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from landed.db.models import Base, Project, User
from landed.db.session import build_engine


@pytest.fixture
def db() -> Iterator[Session]:
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


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
