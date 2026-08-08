"""Projects: the dashboard, a project's evidence, and its comparison versions."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from landed.db.models import User
from landed.services import chat, comparisons, projects, resolutions
from landed.services.projects import ProjectNotFound
from landed.web.security import csrf_token, db_session, require_user, verify_csrf
from landed.web.templating import templates

router = APIRouter(tags=["projects"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.get("/")
async def dashboard(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    owned = projects.list_projects(session, user)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "projects": owned,
            "summaries": {p.id: _summary(session, user, p.id) for p in owned},
            "csrf": csrf_token(request),
        },
    )


@router.post("/projects", dependencies=[Depends(verify_csrf)])
async def create(
    request: Request,
    name: str = Form(...),
    product_name: str = Form(default=""),
    base_currency: str = Form(default="USD"),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    project = projects.create_project(session, user, name, product_name, base_currency)
    return RedirectResponse(
        f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{project_id}")
async def detail(
    request: Request,
    project_id: int,
    version: int | None = None,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    project = projects.get_project(session, user, project_id)
    comparison = comparisons.get_version(session, user, project_id, version)
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "user": user,
            "project": project,
            "documents": projects.list_documents(session, user, project_id),
            "comparison": comparison,
            "results": _ordered(comparison),
            "versions": comparisons.list_versions(session, user, project_id),
            "history": resolutions.history(session, user, project_id),
            "messages": chat.history(session, user, project_id),
            "csrf": csrf_token(request),
        },
    )


@router.post("/projects/{project_id}/documents", dependencies=[Depends(verify_csrf)])
async def upload(
    project_id: int,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    for upload_file in files:
        content = await upload_file.read()
        if not content or len(content) > MAX_UPLOAD_BYTES:
            continue
        projects.add_document(
            session,
            user,
            project_id,
            upload_file.filename or "upload",
            content,
            content_type=upload_file.content_type,
        )
    return _back_to(project_id)


@router.get("/projects/{project_id}/documents/{document_id}/open")
async def open_document(
    project_id: int,
    document_id: int,
    download: bool = False,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Serve an uploaded document so a citation can be checked against the source.

    The path comes from the database row, never from the request. Files are stored
    under a content hash precisely so a supplied filename can never reach the
    filesystem, and resolving one here would give that back.
    """
    document = projects.get_document(session, user, project_id, document_id)
    stored = Path(document.stored_path)
    if not stored.is_file():
        raise ProjectNotFound(document_id)

    disposition = "attachment" if download else "inline"
    return FileResponse(
        stored,
        media_type=document.content_type or _guess_type(document.filename),
        filename=document.filename,
        content_disposition_type=disposition,
        headers={
            # Uploaded files are untrusted content served from our own origin. Without
            # this, an HTML or SVG upload would run script against a logged-in session.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


def _guess_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


@router.post(
    "/projects/{project_id}/documents/{document_id}/remove",
    dependencies=[Depends(verify_csrf)],
)
async def remove_document(
    project_id: int,
    document_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    projects.remove_document(session, user, project_id, document_id)
    return _back_to(project_id)


@router.post("/projects/{project_id}/run", dependencies=[Depends(verify_csrf)])
async def run(
    project_id: int,
    quantity: int = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    comparison = comparisons.run_and_save(session, user, project_id, quantity)
    return _back_to(project_id, comparison.version)


@router.post("/projects/{project_id}/resolve", dependencies=[Depends(verify_csrf)])
async def resolve(
    project_id: int,
    supplier_id: str = Form(...),
    field_path: str = Form(...),
    value: str = Form(...),
    currency: str = Form(default=""),
    chosen_file: str = Form(default=""),
    rationale: str = Form(default=""),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Supply a missing value, or settle a contradiction by naming a source.

    Recorded only. The next run applies it, so the stored comparison a report was
    issued from is never mutated after the fact.
    """
    if chosen_file:
        resolutions.choose_source(
            session, user, project_id, supplier_id, field_path, value,
            chosen_file, rationale,
        )
    else:
        resolutions.supply_value(
            session, user, project_id, supplier_id, field_path, value,
            currency=currency or None, rationale=rationale,
        )
    return _back_to(project_id)


@router.post(
    "/projects/{project_id}/resolutions/{resolution_id}/revert",
    dependencies=[Depends(verify_csrf)],
)
async def revert(
    project_id: int,
    resolution_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    resolutions.revert(session, user, project_id, resolution_id)
    return _back_to(project_id)


@router.post("/projects/{project_id}/ask", dependencies=[Depends(verify_csrf)])
async def ask(
    project_id: int,
    question: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    if question.strip():
        chat.ask(session, user, project_id, question)
    return _back_to(project_id)


@router.post(
    "/projects/{project_id}/chat/{message_id}/confirm",
    dependencies=[Depends(verify_csrf)],
)
async def confirm_action(
    project_id: int,
    message_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Carry out a change the assistant proposed.

    The confirmation is the approval step. A model turning "freight is about twenty
    four hundred" straight into a recorded assumption would put a value nobody
    actually approved into the audit trail.
    """
    chat.confirm(session, user, project_id, message_id)
    return _back_to(project_id)


@router.get("/projects/{project_id}/diff")
async def show_diff(
    request: Request,
    project_id: int,
    base: int,
    target: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    project = projects.get_project(session, user, project_id)
    previous = comparisons.get_version(session, user, project_id, base)
    current = comparisons.get_version(session, user, project_id, target)
    if previous is None or current is None:
        return _back_to(project_id)
    return templates.TemplateResponse(
        request,
        "diff.html",
        {
            "user": user,
            "project": project,
            "diff": comparisons.diff(previous, current),
            "csrf": csrf_token(request),
        },
    )


@router.post("/projects/{project_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete(
    project_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    projects.delete_project(session, user, project_id)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def _back_to(project_id: int, version: int | None = None):
    """Redirect after a mutation so a refresh cannot resubmit it."""
    suffix = f"?version={version}" if version else ""
    return RedirectResponse(
        f"/projects/{project_id}{suffix}", status_code=status.HTTP_303_SEE_OTHER
    )


def _ordered(comparison) -> list:
    """Costed suppliers cheapest first, then everything still blocked.

    Blocked suppliers are never interleaved by price — they have no price, and
    placing them among those that do implies one.
    """
    if comparison is None:
        return []
    costed = [r for r in comparison.results if r.breakdown]
    blocked = [r for r in comparison.results if not r.breakdown]
    costed.sort(key=lambda r: float(r.breakdown["per_unit"]["value"]))
    blocked.sort(key=lambda r: r.supplier_id)
    return costed + blocked


def _summary(session: Session, user: User, project_id: int) -> dict:
    """Counts for the dashboard card."""
    comparison = comparisons.get_version(session, user, project_id)
    if comparison is None:
        return {"version": None, "landed": 0, "total": 0}
    return {
        "version": comparison.version,
        "landed": sum(1 for r in comparison.results if r.state == "landed"),
        "total": len(comparison.results),
        "quantity": comparison.quantity,
        "currency": comparison.currency,
    }
