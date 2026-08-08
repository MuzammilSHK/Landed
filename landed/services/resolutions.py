"""Recording and reversing human decisions.

The audit trail behind every value a document did not provide. Reversal writes
`reverted_at` rather than deleting: the record of what was once assumed has to
survive the reversal, or a report issued under an assumption becomes unexplainable
after someone withdraws it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from landed.core.resolutions import HumanResolution
from landed.core.schema import Attribution
from landed.db.models import Resolution, User
from landed.services.projects import ProjectNotFound, get_project

ASSUMPTION = "assumption"
SOURCE_CHOICE = "source_choice"


def supply_value(
    session: Session,
    user: User,
    project_id: int,
    supplier_id: str,
    field_path: str,
    value: str,
    unit: str | None = None,
    currency: str | None = None,
    rationale: str | None = None,
) -> Resolution:
    """Record a value a person supplied for a field no document states."""
    return _record(
        session,
        user,
        project_id,
        supplier_id,
        field_path,
        kind=ASSUMPTION,
        payload={"value": value, "unit": unit, "currency": currency},
        rationale=rationale,
    )


def choose_source(
    session: Session,
    user: User,
    project_id: int,
    supplier_id: str,
    field_path: str,
    value: str,
    chosen_file: str,
    rationale: str | None = None,
) -> Resolution:
    """Record which document a person sided with when two disagreed.

    Storing the file, not just the value, is what makes the trail meaningful: not
    that someone picked 10,000, but that they preferred the profile to the quotation.
    """
    return _record(
        session,
        user,
        project_id,
        supplier_id,
        field_path,
        kind=SOURCE_CHOICE,
        payload={"value": value, "chosen_file": chosen_file},
        rationale=rationale,
    )


def revert(session: Session, user: User, project_id: int, resolution_id: int) -> Resolution:
    """Withdraw a decision without erasing that it was made."""
    project = get_project(session, user, project_id)
    resolution = session.scalars(
        select(Resolution).where(
            Resolution.id == resolution_id, Resolution.project_id == project.id
        )
    ).one_or_none()
    if resolution is None:
        raise ProjectNotFound(resolution_id)
    if resolution.is_active:
        resolution.reverted_at = datetime.now(UTC)
        session.commit()
    return resolution


def history(session: Session, user: User, project_id: int) -> list[Resolution]:
    """Every decision ever recorded, reverted ones included, newest first.

    Ordered by id as well as timestamp. PostgreSQL's now() is transaction-scoped, so
    two decisions recorded in one transaction carry the same created_at and would
    otherwise come back in an arbitrary order — which for a supersede-by-recency rule
    means the wrong value wins.
    """
    project = get_project(session, user, project_id)
    return list(
        session.scalars(
            select(Resolution)
            .where(Resolution.project_id == project.id)
            .order_by(Resolution.created_at.desc(), Resolution.id.desc())
        )
    )


def active(session: Session, user: User, project_id: int) -> list[HumanResolution]:
    """Decisions still standing, as the core-level objects the pipeline applies.

    Later decisions win: supplying a value twice is a correction, not a conflict.
    """
    latest: dict[tuple[str, str], Resolution] = {}
    for row in reversed(history(session, user, project_id)):
        if row.is_active:
            latest[(row.supplier_id, row.field_path)] = row
    return [_to_core(row) for row in latest.values()]


def _record(
    session: Session,
    user: User,
    project_id: int,
    supplier_id: str,
    field_path: str,
    kind: str,
    payload: dict,
    rationale: str | None,
) -> Resolution:
    project = get_project(session, user, project_id)
    resolution = Resolution(
        project_id=project.id,
        supplier_id=supplier_id,
        field_path=field_path,
        kind=kind,
        payload=payload,
        actor_email=user.email,
        rationale=(rationale or "").strip() or None,
    )
    session.add(resolution)
    session.commit()
    return resolution


def _to_core(row: Resolution) -> HumanResolution:
    return HumanResolution(
        supplier_id=row.supplier_id,
        field_path=row.field_path,
        value=str(row.payload.get("value", "")),
        unit=row.payload.get("unit"),
        currency=row.payload.get("currency"),
        chosen_file=row.payload.get("chosen_file"),
        attribution=Attribution(
            actor=row.actor_email, at=row.created_at, rationale=row.rationale
        ),
    )
