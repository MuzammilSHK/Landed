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
) -> Project:
    project = Project(
        user_id=user.id,
        name=name.strip(),
        product_name=(product_name or "").strip() or None,
        base_currency=base_currency.upper()[:3],
        destination_country=(destination_country or "").strip() or None,
    )
    session.add(project)
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


def remove_document(session: Session, user: User, project_id: int, document_id: int) -> None:
    """Detach a document from the project.

    The stored bytes are left in place: earlier comparison versions cite them, and a
    citation that no longer resolves is worse than an orphaned file.
    """
    project = get_project(session, user, project_id)
    document = session.scalars(
        select(Document).where(
            Document.id == document_id, Document.project_id == project.id
        )
    ).one_or_none()
    if document is None:
        raise ProjectNotFound(document_id)
    session.delete(document)
    session.commit()
