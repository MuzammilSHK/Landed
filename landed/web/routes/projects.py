"""Projects: the dashboard, a project's evidence, and its comparison versions."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from landed.core.providers import ProviderError
from landed.db.models import User
from landed.services import chat, comparisons, projects, resolutions, suppliers
from landed.services.comparisons import NoDocuments
from landed.services.projects import ProjectNotFound
from landed.web.security import csrf_token, db_session, require_user, verify_csrf
from landed.web.templating import templates

router = APIRouter(tags=["projects"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# What the comparison needs, in the order someone actually assembles it. Rendered as
# a checklist on the project page: the previous version told you nothing about what to
# upload, so the first run of a real project produced an empty comparison and no
# explanation of why.
REQUIRED_DOCUMENTS = [
    {
        "kind": "quotation",
        "title": "A quotation, per supplier",
        "detail": "The priced offer itself — unit price, currency, delivery term "
                  "(FOB, DDP…), MOQ, tooling, validity. PDF, Excel, Word, CSV, or a "
                  "photo of a printed quote.",
        "required": True,
    },
    {
        "kind": "profile",
        "title": "A supplier profile, optional",
        "detail": "Capability sheet, certifications, capacity, lead time. Its value "
                  "is disagreement: where it contradicts the quotation, both values "
                  "are shown and neither is chosen for you.",
        "required": False,
    },
]

# Messages surfaced after a redirect. Codes rather than free text so a crafted URL
# cannot put arbitrary wording on somebody's screen.
NOTICES = {
    "no-documents": "Add at least one supplier and upload a quotation before comparing.",
    "extraction-failed": "The document reader could not be reached. Nothing was saved "
                         "— check the provider settings in .env and try again.",
    "supplier-needed": "Name the supplier first, then upload their quotation into "
                       "that supplier's column.",
    "compared": "Comparison complete.",
    "saved": "Saved. It takes effect on the next comparison.",
}


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
    target_quantity: int = Form(default=10000),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    project = projects.create_project(
        session, user, name, product_name, base_currency, target_quantity=target_quantity
    )
    return RedirectResponse(
        f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{project_id}")
async def detail(
    request: Request,
    project_id: int,
    version: int | None = None,
    notice: str | None = None,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    project = projects.get_project(session, user, project_id)
    # Results are shown only when a version is explicitly asked for — which the
    # redirect after a run does. A plain reload therefore comes back clean rather than
    # re-presenting an older run as if it were current: figures on screen get acted
    # on, and stale ones are the dangerous kind.
    comparison = (
        comparisons.get_version(session, user, project_id, version)
        if version is not None
        else None
    )
    listed = suppliers.list_suppliers(session, user, project_id)
    grouped, shared = suppliers.documents_by_supplier(session, user, project_id)
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "user": user,
            "project": project,
            "suppliers": listed,
            "supplier_documents": grouped,
            "shared_documents": shared,
            "documents": projects.list_documents(session, user, project_id),
            "required_documents": REQUIRED_DOCUMENTS,
            # Shown read-only beneath the comparison. A total computed from an
            # assumption the reader cannot see is a total they cannot check.
            "assumptions_used": [
                (projects.ASSUMPTION_LABELS.get(field, field), value)
                for field, value in projects.effective_assumptions(project).items()
            ],
            "comparison": comparison,
            "results": _ordered(comparison),
            "results_by_code": _by_code(comparison),
            "versions": comparisons.list_versions(session, user, project_id),
            "history": resolutions.history(session, user, project_id),
            "messages": chat.history(session, user, project_id),
            "notice": NOTICES.get(notice or ""),
            "csrf": csrf_token(request),
        },
    )


@router.post("/projects/{project_id}/suppliers", dependencies=[Depends(verify_csrf)])
async def add_supplier(
    project_id: int,
    name: str = Form(...),
    country: str = Form(default=""),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    if not name.strip():
        return _back_to(project_id, notice="supplier-needed")
    suppliers.add_supplier(session, user, project_id, name, country)
    return _back_to(project_id)


@router.post(
    "/projects/{project_id}/suppliers/{supplier_id}",
    dependencies=[Depends(verify_csrf)],
)
async def edit_supplier(
    project_id: int,
    supplier_id: int,
    name: str = Form(...),
    country: str = Form(default=""),
    notes: str = Form(default=""),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Correct a supplier's details.

    The code is never changed — stored comparison versions reference it, and a report
    already issued has to keep resolving to the supplier it was about.
    """
    supplier = suppliers.rename_supplier(
        session, user, project_id, supplier_id, name, country
    )
    supplier.notes = notes.strip() or None
    session.commit()
    return _back_to(project_id, notice="saved")


@router.post(
    "/projects/{project_id}/suppliers/{supplier_id}/remove",
    dependencies=[Depends(verify_csrf)],
)
async def remove_supplier(
    project_id: int,
    supplier_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    suppliers.remove_supplier(session, user, project_id, supplier_id)
    return _back_to(project_id)


@router.post("/projects/{project_id}/documents", dependencies=[Depends(verify_csrf)])
async def upload(
    project_id: int,
    files: list[UploadFile] = File(...),
    supplier_ref_id: str = Form(default=""),
    kind: str = Form(default="quotation"),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Store uploaded files, filed under the supplier the user chose.

    An empty `supplier_ref_id` means shared project material. The supplier is named at
    upload time rather than inferred from the filename: a buyer's quote is called
    `Shenzhen_Q3_revised.pdf`, and inferring from that is how a supplier used to
    disappear from the comparison without anything being reported.
    """
    ref = (
        suppliers.get_supplier(session, user, project_id, int(supplier_ref_id)).id
        if supplier_ref_id.isdigit()
        else None
    )
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
            kind=kind if kind in {"quotation", "profile"} else "shared",
            content_type=upload_file.content_type,
            supplier_ref_id=ref,
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
    quantity: int = Form(default=0),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Run a comparison, or say why one could not be run.

    Both failures here are ordinary states of a half-assembled project, not faults.
    They used to reach the user as a 500 page, which is the least informative possible
    answer from a product whose entire claim is that it explains what is missing.
    """
    if quantity > 0:
        projects.set_quantity(session, user, project_id, quantity)
    try:
        comparison = comparisons.run_and_save(session, user, project_id, quantity or None)
    except NoDocuments:
        return _back_to(project_id, notice="no-documents")
    except ProviderError:
        return _back_to(project_id, notice="extraction-failed")
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
    version: int | None = Form(default=None),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Answer a question and come back to the conversation, not to the top.

    `version` is carried through so asking about a comparison does not clear the
    comparison off the screen — the question is almost always about what is showing.
    """
    if question.strip():
        chat.ask(session, user, project_id, question)
    return _back_to(project_id, version, anchor="ask")


@router.post(
    "/projects/{project_id}/chat/{message_id}/confirm",
    dependencies=[Depends(verify_csrf)],
)
async def confirm_action(
    project_id: int,
    message_id: int,
    version: int | None = Form(default=None),
    user: User = Depends(require_user),
    session: Session = Depends(db_session),
):
    """Carry out a change the assistant proposed.

    The confirmation is the approval step. A model turning "freight is about twenty
    four hundred" straight into a recorded assumption would put a value nobody
    actually approved into the audit trail.
    """
    chat.confirm(session, user, project_id, message_id)
    return _back_to(project_id, version, anchor="ask")


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


def _back_to(
    project_id: int,
    version: int | None = None,
    notice: str | None = None,
    anchor: str | None = None,
):
    """Redirect after a mutation so a refresh cannot resubmit it.

    `anchor` returns the reader to the part of the page they were working in. Without
    it, asking a question landed them back at the top of a long page with no idea the
    answer had arrived further down.
    """
    parts = []
    if version:
        parts.append(f"version={version}")
    if notice:
        parts.append(f"notice={notice}")
    suffix = f"?{'&'.join(parts)}" if parts else ""
    fragment = f"#{anchor}" if anchor else ""
    return RedirectResponse(
        f"/projects/{project_id}{suffix}{fragment}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _by_code(comparison) -> dict:
    """Results keyed by supplier code, for rendering one column per supplier."""
    if comparison is None:
        return {}
    return {result.supplier_id: result for result in comparison.results}


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
    listed = len(suppliers.list_suppliers(session, user, project_id))
    if comparison is None:
        return {"version": None, "landed": 0, "total": 0, "suppliers": listed}
    return {
        "version": comparison.version,
        "landed": sum(1 for r in comparison.results if r.state == "landed"),
        "total": len(comparison.results),
        "suppliers": listed,
        "quantity": comparison.quantity,
        "currency": comparison.currency,
    }
