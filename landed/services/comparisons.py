"""Running, storing, and comparing comparison versions.

Versions are append-only. A report sent last week has to still say what it said, so
re-running a project never rewrites a stored result — it adds one. That is also what
makes the diff possible, and the diff is the question a sourcing team actually asks:
a supplier revised their quote, did the decision change?

A refusal is stored as a result, not an absent row. "We declined to cost this" is
information the next version has to be able to compare against.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from landed.core.ingest import ingest_file
from landed.core.pipeline import ComparisonOutcome, compare_documents
from landed.core.providers import Provider
from landed.core.schema import CostAssumptions, QuoteState
from landed.db.models import Comparison, ComparisonResult, Document, User
from landed.services import resolutions
from landed.services.projects import get_project


class NoDocuments(Exception):
    """A project with nothing uploaded cannot be compared."""


def run_and_save(
    session: Session,
    user: User,
    project_id: int,
    quantity: int,
    provider: Provider | None = None,
    assumptions: CostAssumptions | None = None,
) -> Comparison:
    """Compare every document in the project and store the result as a new version."""
    project = get_project(session, user, project_id)
    documents = list(
        session.scalars(select(Document).where(Document.project_id == project.id))
    )
    if not documents:
        raise NoDocuments(project_id)

    ingested = [ingest_file(Path(d.stored_path)) for d in documents]
    for original, read in zip(documents, ingested, strict=True):
        # Uploads are stored under a content hash, so restore the name the user knows.
        read.filename = original.filename

    outcome = compare_documents(
        ingested,
        quantity,
        provider,
        assumptions,
        resolutions=resolutions.active(session, user, project.id),
    )
    return save(session, project.id, outcome, [d.id for d in documents])


def save(
    session: Session,
    project_id: int,
    outcome: ComparisonOutcome,
    document_ids: list[int],
) -> Comparison:
    comparison = Comparison(
        project_id=project_id,
        version=next_version(session, project_id),
        quantity=outcome.quantity,
        currency=outcome.currency,
        assumptions=outcome.assumptions.model_dump(mode="json"),
        document_ids=document_ids,
    )
    session.add(comparison)
    session.flush()

    for supplier in outcome.suppliers:
        session.add(
            ComparisonResult(
                comparison_id=comparison.id,
                supplier_id=supplier.supplier_id,
                supplier_name=supplier.supplier_name,
                state=supplier.state.value,
                breakdown=(
                    supplier.breakdown.model_dump(mode="json")
                    if supplier.breakdown
                    else None
                ),
                refusal=(
                    supplier.refusal.model_dump(mode="json") if supplier.refusal else None
                ),
                conflicts=[c.model_dump(mode="json") for c in supplier.quotation.conflicts],
            )
        )
    session.commit()
    return comparison


def next_version(session: Session, project_id: int) -> int:
    highest = session.scalar(
        select(func.max(Comparison.version)).where(Comparison.project_id == project_id)
    )
    return (highest or 0) + 1


def list_versions(session: Session, user: User, project_id: int) -> list[Comparison]:
    project = get_project(session, user, project_id)
    return list(
        session.scalars(
            select(Comparison)
            .where(Comparison.project_id == project.id)
            .order_by(Comparison.version.desc())
        )
    )


def get_version(
    session: Session, user: User, project_id: int, version: int | None = None
) -> Comparison | None:
    """A specific version, or the latest when none is named."""
    project = get_project(session, user, project_id)
    query = select(Comparison).where(Comparison.project_id == project.id)
    query = (
        query.where(Comparison.version == version)
        if version is not None
        else query.order_by(Comparison.version.desc()).limit(1)
    )
    return session.scalars(query).one_or_none()


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #

class SupplierChange(BaseModel):
    """How one supplier moved between two versions."""

    supplier_id: str
    supplier_name: str | None = None
    previous_state: str | None = None
    current_state: str | None = None
    previous_per_unit: Decimal | None = None
    current_per_unit: Decimal | None = None
    resolved_conflicts: list[str] = Field(default_factory=list)
    new_conflicts: list[str] = Field(default_factory=list)

    @property
    def delta(self) -> Decimal | None:
        if self.previous_per_unit is None or self.current_per_unit is None:
            return None
        return self.current_per_unit - self.previous_per_unit

    @property
    def delta_percent(self) -> Decimal | None:
        if not self.previous_per_unit or self.delta is None:
            return None
        return (self.delta / self.previous_per_unit) * 100

    @property
    def became_comparable(self) -> bool:
        """Blocked before, costed now — the change a buyer is waiting for."""
        return (
            self.previous_state != QuoteState.LANDED.value
            and self.current_state == QuoteState.LANDED.value
        )

    @property
    def became_blocked(self) -> bool:
        return (
            self.previous_state == QuoteState.LANDED.value
            and self.current_state not in (None, QuoteState.LANDED.value)
        )

    @property
    def is_new(self) -> bool:
        return self.previous_state is None

    @property
    def is_gone(self) -> bool:
        return self.current_state is None

    @property
    def has_moved(self) -> bool:
        return bool(
            self.delta
            or self.previous_state != self.current_state
            or self.resolved_conflicts
            or self.new_conflicts
        )


class ComparisonDiff(BaseModel):
    """What changed between two versions, and whether it mattered."""

    from_version: int
    to_version: int
    changes: list[SupplierChange] = Field(default_factory=list)
    previous_recommendation: str | None = None
    current_recommendation: str | None = None

    @property
    def recommendation_changed(self) -> bool:
        return self.previous_recommendation != self.current_recommendation

    @property
    def moved(self) -> list[SupplierChange]:
        return [change for change in self.changes if change.has_moved]

    @property
    def is_material(self) -> bool:
        """Worth showing at all — nothing moved means nothing to report."""
        return bool(self.moved) or self.recommendation_changed


def diff(previous: Comparison, current: Comparison) -> ComparisonDiff:
    """Compare two stored versions.

    Suppliers present in only one version are reported as appearing or disappearing
    rather than skipped: a supplier dropping out of a comparison is exactly the sort
    of change that should not pass unnoticed.
    """
    before = {r.supplier_id: r for r in previous.results}
    after = {r.supplier_id: r for r in current.results}

    changes = [
        _change(supplier_id, before.get(supplier_id), after.get(supplier_id))
        for supplier_id in sorted(before | after)
    ]
    return ComparisonDiff(
        from_version=previous.version,
        to_version=current.version,
        changes=changes,
        previous_recommendation=_recommendation(previous),
        current_recommendation=_recommendation(current),
    )


def _change(
    supplier_id: str,
    before: ComparisonResult | None,
    after: ComparisonResult | None,
) -> SupplierChange:
    before_messages = _conflict_messages(before)
    after_messages = _conflict_messages(after)
    return SupplierChange(
        supplier_id=supplier_id,
        supplier_name=(after or before).supplier_name,
        previous_state=before.state if before else None,
        current_state=after.state if after else None,
        previous_per_unit=_per_unit(before),
        current_per_unit=_per_unit(after),
        resolved_conflicts=sorted(before_messages - after_messages),
        new_conflicts=sorted(after_messages - before_messages),
    )


def _per_unit(result: ComparisonResult | None) -> Decimal | None:
    if result is None or not result.breakdown:
        return None
    return Decimal(str(result.breakdown["per_unit"]["value"]))


def _conflict_messages(result: ComparisonResult | None) -> set[str]:
    if result is None:
        return set()
    return {c["message"] for c in result.conflicts or [] if c.get("resolved_with") is None}


def _recommendation(comparison: Comparison) -> str | None:
    """Cheapest landed supplier, or none if nothing could be costed."""
    costed = [
        (r.supplier_id, _per_unit(r))
        for r in comparison.results
        if r.state == QuoteState.LANDED.value and _per_unit(r) is not None
    ]
    return min(costed, key=lambda pair: pair[1])[0] if costed else None
