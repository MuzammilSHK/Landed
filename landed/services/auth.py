"""Accounts and password verification.

Argon2id, at the library's defaults. Passwords are never stored, never logged, and
never returned — only their hash reaches the database.

Two details that are easy to omit and awkward to add later: a failed lookup still
performs a hash verification, so response time does not reveal whether an address is
registered; and a hash produced under older parameters is upgraded transparently on
the next successful sign-in.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from landed.db.models import User

MINIMUM_PASSWORD_LENGTH = 10

# Verified against when no account matches, so a missing address costs the same time
# as a wrong password. The value is irrelevant; only the work it forces matters.
_DUMMY_HASH = PasswordHasher().hash("landed-timing-equaliser")

_hasher = PasswordHasher()


class EmailAlreadyRegistered(Exception):
    """Raised on registration; never surfaced during sign-in.

    Sign-in must not disclose which addresses exist.
    """


class WeakPassword(Exception):
    pass


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def register(session: Session, email: str, password: str) -> User:
    """Create an account. Email uniqueness is enforced by the database.

    The IntegrityError path matters: checking first and inserting after leaves a race
    where two concurrent registrations both pass the check.
    """
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    user = User(email=email.strip(), password_hash=hash_password(password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise EmailAlreadyRegistered(email) from exc
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Return the user when the credentials match, otherwise None.

    Never distinguishes "no such account" from "wrong password", in the return value
    or in the time taken.
    """
    user = find_by_email(session, email)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    _upgrade_hash_if_needed(session, user, password)
    return user


def find_by_email(session: Session, email: str) -> User | None:
    """Case-insensitive by virtue of the CITEXT column, not by lowering here."""
    return session.scalars(select(User).where(User.email == email.strip())).one_or_none()


def change_password(session: Session, user: User, new_password: str) -> None:
    if len(new_password) < MINIMUM_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    user.password_hash = hash_password(new_password)
    session.commit()


def _upgrade_hash_if_needed(session: Session, user: User, password: str) -> None:
    """Re-hash under current parameters when the stored hash predates them."""
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.commit()
