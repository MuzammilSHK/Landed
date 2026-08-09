"""The supplier list a project is comparing.

A supplier is on the list because someone put it there, not because a file happened
to be named a certain way. That inversion is the point: the column exists first, and
a missing or unreadable quotation is then a visible gap inside it rather than a
supplier that quietly never appeared in the comparison at all.

Codes are short, uppercase, and unique within a project. They are what the cost
engine, the conflict records, and every stored comparison result carry, so they have
to be stable — renaming a supplier changes its display name and leaves its code and
therefore its history intact.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from landed.db.models import Document, Supplier, User
from landed.services.projects import ProjectNotFound, get_project

CODE_ALLOWED = re.compile(r"[^A-Z0-9]+")


class DuplicateSupplier(Exception):
    """A code already in use within this project."""


def code_from_name(name: str) -> str:
    """Derive a stable short code from a supplier's name.

    "Shenzhen Precision Metalworks" becomes SHENZHENPRECISION. Readable in a citation
    and in a stored result, which a surrogate integer would not be.
    """
    cleaned = CODE_ALLOWED.sub("", name.upper())
    return (cleaned[:18] or "SUPPLIER")


def list_suppliers(session: Session, user: User, project_id: int) -> list[Supplier]:
    project = get_project(session, user, project_id)
    return list(
        session.scalars(
            select(Supplier)
            .where(Supplier.project_id == project.id)
            .order_by(Supplier.created_at, Supplier.id)
        )
    )


def add_supplier(
    session: Session,
    user: User,
    project_id: int,
    name: str,
    country: str | None = None,
    code: str | None = None,
) -> Supplier:
    project = get_project(session, user, project_id)
    name = name.strip()
    if not name:
        raise ValueError("a supplier needs a name")

    wanted = (code or code_from_name(name)).strip().upper()[:80]
    taken = {s.code for s in list_suppliers(session, user, project_id)}
    if wanted in taken:
        # Two plants of the same group are a normal thing to compare, so a collision
        # is disambiguated rather than rejected.
        wanted = _next_free(wanted, taken)

    supplier = Supplier(
        project_id=project.id,
        code=wanted,
        name=name,
        country=(country or "").strip() or None,
    )
    session.add(supplier)
    session.commit()
    return supplier


def _next_free(code: str, taken: set[str]) -> str:
    for suffix in range(2, 100):
        candidate = f"{code[:76]}-{suffix}"
        if candidate not in taken:
            return candidate
    raise DuplicateSupplier(code)


def get_supplier(
    session: Session, user: User, project_id: int, supplier_id: int
) -> Supplier:
    project = get_project(session, user, project_id)
    supplier = session.scalars(
        select(Supplier).where(
            Supplier.id == supplier_id, Supplier.project_id == project.id
        )
    ).one_or_none()
    if supplier is None:
        raise ProjectNotFound(supplier_id)
    return supplier


def rename_supplier(
    session: Session,
    user: User,
    project_id: int,
    supplier_id: int,
    name: str,
    country: str | None = None,
) -> Supplier:
    """Change how a supplier is displayed, never its code.

    Stored comparison results reference the code. Rewriting it would orphan every
    version already issued, and a version that no longer matches the report sent from
    it is worse than an out-of-date name.
    """
    supplier = get_supplier(session, user, project_id, supplier_id)
    if name.strip():
        supplier.name = name.strip()
    supplier.country = (country or "").strip() or None
    session.commit()
    return supplier


def remove_supplier(
    session: Session, user: User, project_id: int, supplier_id: int
) -> None:
    """Take a supplier off the list.

    Its documents stay on the project, unattached. Deleting the bytes would break the
    citations in comparison versions already issued against them.
    """
    session.delete(get_supplier(session, user, project_id, supplier_id))
    session.commit()


def documents_by_supplier(
    session: Session, user: User, project_id: int
) -> tuple[dict[int, list[Document]], list[Document]]:
    """Every document filed under a supplier, plus the shared ones filed under none."""
    project = get_project(session, user, project_id)
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.project_id == project.id)
            .order_by(Document.filename)
        )
    )
    grouped: dict[int, list[Document]] = {}
    shared: list[Document] = []
    for document in documents:
        if document.supplier_ref_id is None:
            shared.append(document)
        else:
            grouped.setdefault(document.supplier_ref_id, []).append(document)
    return grouped, shared
