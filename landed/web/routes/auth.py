"""Registration, sign-in, sign-out."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from landed.services import auth as service
from landed.web.security import (
    csrf_token,
    db_session,
    login_user,
    logout_user,
    optional_user,
    verify_csrf,
)
from landed.web.templating import templates

router = APIRouter(tags=["auth"])

# Shown for every failed sign-in, whatever the cause. Distinguishing "no such
# account" from "wrong password" tells a prober which addresses are registered.
SIGN_IN_FAILED = "Those details do not match an account."


@router.get("/login")
async def login_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    # Minting the token here is what puts one in the form. Reading it straight from
    # the session in the template yields an empty value on a first visit, so every
    # sign-in would be rejected as a forgery.
    return templates.TemplateResponse(
        request, "login.html", {"mode": "login", "csrf": csrf_token(request)}
    )


@router.post("/login", dependencies=[Depends(verify_csrf)])
async def sign_in(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(db_session),
):
    user = service.authenticate(session, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "mode": "login",
                "error": SIGN_IN_FAILED,
                "email": email,
                "csrf": csrf_token(request),
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/register")
async def register_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"mode": "register", "csrf": csrf_token(request)}
    )


@router.post("/register", dependencies=[Depends(verify_csrf)])
async def sign_up(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(db_session),
):
    try:
        user = service.register(session, email, password)
    except service.WeakPassword as exc:
        return _rejected(request, email, str(exc))
    except service.EmailAlreadyRegistered:
        return _rejected(request, email, "That address is already registered.")
    login_user(request, user)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def sign_out(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def _rejected(request: Request, email: str, message: str):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"mode": "register", "error": message, "email": email, "csrf": csrf_token(request)},
        status_code=status.HTTP_400_BAD_REQUEST,
    )
