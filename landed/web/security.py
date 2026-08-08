"""Sessions, the current user, and CSRF.

The session cookie holds a user id and nothing else. Anything richer would need
invalidating when it changes, and a stale copy of a permission is how a revoked
account keeps working.

CSRF uses a per-session token echoed in a hidden form field. Without it, a page on
another origin can make the browser submit a form here with the user's cookie
attached — and every mutating route in this app changes evidence a decision rests on.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from landed.db.models import User
from landed.db.session import get_session

SESSION_USER_KEY = "user_id"
SESSION_CSRF_KEY = "csrf"
CSRF_FIELD = "csrf_token"


class RedirectToLogin(HTTPException):
    """Raised when an anonymous request reaches a page that needs an account."""

    def __init__(self, destination: str = "/login") -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER, headers={"Location": destination}
        )


def login_user(request: Request, user: User) -> None:
    """Start a session, replacing any token from before sign-in.

    Rotating the CSRF token here prevents a token fixed by an attacker before login
    from remaining valid afterwards.
    """
    request.session[SESSION_USER_KEY] = user.id
    request.session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)


def logout_user(request: Request) -> None:
    request.session.clear()


def db_session() -> Iterator[Session]:
    yield from get_session()


def optional_user(
    request: Request, session: Session = Depends(db_session)
) -> User | None:
    """The signed-in user, or None. Used by pages that render either way."""
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    if user is None:
        # The account was deleted while the cookie lived on.
        request.session.clear()
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise RedirectToLogin()
    return user


def csrf_token(request: Request) -> str:
    """The session's token, minted on first use."""
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def verify_csrf(request: Request, csrf_token: str = Form(default="")) -> None:
    """Dependency for every mutating route.

    Compared in constant time, and a session with no token rejects rather than
    matching an empty submission.
    """
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected or not secrets.compare_digest(str(csrf_token), str(expected)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This form has expired. Reload the page and try again.",
        )
