"""Projects, documents, and the ownership rule.

Every accessor here takes the acting user and scopes on them. There is no
`get_project(session, project_id)` without a user, deliberately: the one-argument
convenience is how a route handler eventually reads somebody else's data.

Missing and forbidden are the same outcome — `ProjectNotFound` either way, so a
probe cannot distinguish an id that does not exist from one belonging to another
account.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from landed.config import settings
from landed.db.models import Document, Project, User


class ProjectNotFound(Exception):
    """Raised for both a missing project and one owned by someone else."""


def create_project(
    session: Session,
    user: User,
    name: str,
    product_name: str | None = None,
    base_currency: str = "USD",
    destination_country: str | None = None,
    target_quantity: int = 10000,
) -> Project:
    project = Project(
        user_id=user.id,
        name=name.strip(),
        product_name=(product_name or "").strip() or None,
        base_currency=base_currency.upper()[:3],
        destination_country=(destination_country or "").strip() or None,
        target_quantity=max(1, target_quantity),
        # Left empty on purpose: assumptions come from DEFAULT_ASSUMPTIONS at
        # calculation time, so changing that constant changes every project rather
        # than only the ones created afterwards.
        assumptions={},
    )
    session.add(project)
    session.commit()
    return project


# The standing cost assumptions every comparison runs under.
#
# Freight, duty, insurance, and the cost of capital are never stated in a quotation —
# a supplier prices goods, not your logistics — so they have to come from somewhere.
# For now that somewhere is here: one set of figures, matching what the challenge pack
# states, so a demo run and a pack run are arithmetically comparable.
#
# They are applied at calculation time and carry `Origin.ASSUMED` with an attribution
# naming them as a standing default, so no total ever presents one as something a
# supplier's document said. They are also listed under the comparison table, because
# a figure computed from an assumption the reader cannot see is not a figure they can
# check.
DEFAULT_ASSUMPTIONS: dict[str, str] = {
    "freight_flat": "8200",              # flat ocean freight, one 20ft container
    "duty_rate": "0.065",                # 6.5% of CIF value
    "insurance_rate": "0.005",           # 0.5% of goods + freight
    "financing_annual_rate": "0.08",     # 8% annual cost of capital
    "payment_days_outstanding": "60",
    # FX deliberately absent. A rate without a date is refused anyway, and inventing
    # either for a currency we have not seen yet would be guessing at the exact point
    # where guessing changes the ranking.
}

# How each default reads on screen. Kept beside the values so a figure and its label
# cannot drift apart.
ASSUMPTION_LABELS: dict[str, str] = {
    "freight_flat": "Freight, flat",
    "freight_per_kg": "Freight, per kg",
    "duty_rate": "Duty rate",
    "insurance_rate": "Insurance rate",
    "financing_annual_rate": "Cost of capital, annual",
    "fx_rate_to_base": "FX rate",
    "fx_rate_date": "FX rate date",
    "payment_days_outstanding": "Payment days outstanding",
}


def effective_assumptions(project: Project) -> dict:
    """What this project will actually cost with.

    Anything stored on the project wins, which is the seam a per-project assumptions
    form would slot back into. Nothing is stored today, so every project runs on the
    standing defaults — one set of figures, in one place, for every comparison.
    """
    return project.assumptions or dict(DEFAULT_ASSUMPTIONS)


def set_quantity(session: Session, user: User, project_id: int, quantity: int) -> Project:
    """Record the order quantity this project is comparing at.

    Stored on the project rather than on a run: it is a property of the decision, and
    a run carrying its own private copy is how two versions end up incomparable.
    """
    project = get_project(session, user, project_id)
    project.target_quantity = max(1, quantity)
    session.commit()
    return project


def list_projects(session: Session, user: User) -> list[Project]:
    """The dashboard listing: this user's projects, most recently touched first."""
    return list(
        session.scalars(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(Project.updated_at.desc())
        )
    )


def get_project(session: Session, user: User, project_id: int) -> Project:
    project = session.scalars(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    ).one_or_none()
    if project is None:
        raise ProjectNotFound(project_id)
    return project


def rename_project(session: Session, user: User, project_id: int, name: str) -> Project:
    project = get_project(session, user, project_id)
    project.name = name.strip()
    session.commit()
    return project


def delete_project(session: Session, user: User, project_id: int) -> None:
    session.delete(get_project(session, user, project_id))
    session.commit()


def add_document(
    session: Session,
    user: User,
    project_id: int,
    filename: str,
    content: bytes,
    kind: str = "quotation",
    supplier_id: str | None = None,
    content_type: str | None = None,
    supplier_ref_id: int | None = None,
) -> Document:
    """Store an uploaded file and record it against the project.

    Content is written under a hash-derived name rather than the original filename:
    two suppliers both sending `quotation.pdf` must not overwrite each other, and an
    uploaded name is attacker-controlled input that has no business shaping a path.
    """
    project = get_project(session, user, project_id)
    digest = hashlib.sha256(content).hexdigest()

    directory = Path(settings().upload_dir) / str(project.id)
    directory.mkdir(parents=True, exist_ok=True)
    stored = directory / f"{digest}{Path(filename).suffix.lower()}"
    stored.write_bytes(content)

    document = Document(
        project_id=project.id,
        filename=Path(filename).name,
        content_type=content_type,
        kind=kind,
        supplier_id=supplier_id,
        supplier_ref_id=supplier_ref_id,
        sha256=digest,
        byte_size=len(content),
        stored_path=str(stored),
    )
    session.add(document)
    session.commit()
    return document


def list_documents(session: Session, user: User, project_id: int) -> list[Document]:
    project = get_project(session, user, project_id)
    return list(
        session.scalars(
            select(Document)
            .where(Document.project_id == project.id)
            .order_by(Document.filename)
        )
    )


def get_document(
    session: Session, user: User, project_id: int, document_id: int
) -> Document:
    """One document, scoped to its project and its owner."""
    project = get_project(session, user, project_id)
    document = session.scalars(
        select(Document).where(
            Document.id == document_id, Document.project_id == project.id
        )
    ).one_or_none()
    if document is None:
        raise ProjectNotFound(document_id)
    return document


def remove_document(session: Session, user: User, project_id: int, document_id: int) -> None:
    """Detach a document from the project.

    The stored bytes are left in place: earlier comparison versions cite them, and a
    citation that no longer resolves is worse than an orphaned file.
    """
    session.delete(get_document(session, user, project_id, document_id))
    session.commit()
