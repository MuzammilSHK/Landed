"""Versioning and diff tests.

The diff is the product's answer to the question a sourcing team actually asks: a
supplier revised their quote, did the decision change? So the assertions here are
about movement — a supplier becoming comparable, a total shifting, a recommendation
flipping — not about any single version's contents.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from landed.db.models import User
from landed.services import comparisons, projects
from tests.test_pipeline import PACK, PackStub

DOCUMENTS = [
    "quote_a.pdf",
    "quote_b.pdf",
    "profile_b.docx",
    "quote_c.pdf",
    "assumptions.xlsx",
]


@pytest.fixture(autouse=True)
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        projects, "settings", lambda: SimpleNamespace(upload_dir=tmp_path)
    )
    return tmp_path


@pytest.fixture
def project(db: Session, user: User):
    created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
    for name in DOCUMENTS:
        projects.add_document(
            db, user, created.id, name, (PACK / name).read_bytes()
        )
    return created


def run(db: Session, user: User, project, quantity: int = 10_000):
    return comparisons.run_and_save(db, user, project.id, quantity, provider=PackStub())


def result_for(comparison, supplier_id: str):
    return next(r for r in comparison.results if r.supplier_id == supplier_id)


class TestRunning:
    def test_a_run_stores_every_supplier(self, db: Session, user: User, project) -> None:
        comparison = run(db, user, project)
        assert {r.supplier_id for r in comparison.results} == {"A", "B", "C"}

    def test_refusals_are_stored_as_results(self, db: Session, user: User, project) -> None:
        """'We declined to cost this' is what the next version compares against."""
        blocked = result_for(run(db, user, project), "C")
        assert blocked.state == "not_landed"
        assert blocked.breakdown is None
        assert blocked.refusal["missing_fields"]

    def test_original_filenames_survive_the_content_hash_storage(
        self, db: Session, user: User, project
    ) -> None:
        """Citations must name the file the user uploaded, not its digest."""
        landed = result_for(run(db, user, project), "A")
        cited = landed.breakdown["goods"]["source"]
        assert cited is None or cited["file"].endswith(".pdf")
        assert "quote_b.pdf" in str(result_for(run(db, user, project), "B").conflicts)

    def test_a_project_with_no_documents_is_refused(
        self, db: Session, user: User
    ) -> None:
        empty = projects.create_project(db, user, "Empty")
        with pytest.raises(comparisons.NoDocuments):
            comparisons.run_and_save(db, user, empty.id, 10_000, provider=PackStub())


class TestVersioning:
    def test_versions_increment(self, db: Session, user: User, project) -> None:
        assert [run(db, user, project).version for _ in range(3)] == [1, 2, 3]

    def test_rerunning_never_rewrites_an_earlier_version(
        self, db: Session, user: User, project
    ) -> None:
        """A report sent last week has to still say what it said."""
        first = run(db, user, project, quantity=1_000)
        original = result_for(first, "A").breakdown["per_unit"]["value"]
        run(db, user, project, quantity=100_000)
        db.refresh(first)
        assert result_for(first, "A").breakdown["per_unit"]["value"] == original

    def test_latest_is_returned_when_no_version_is_named(
        self, db: Session, user: User, project
    ) -> None:
        run(db, user, project)
        run(db, user, project)
        assert comparisons.get_version(db, user, project.id).version == 2

    def test_versions_are_listed_newest_first(self, db: Session, user: User, project) -> None:
        run(db, user, project)
        run(db, user, project)
        assert [c.version for c in comparisons.list_versions(db, user, project.id)] == [2, 1]

    def test_another_user_cannot_read_your_versions(
        self, db: Session, user: User, project
    ) -> None:
        from landed.services import auth

        run(db, user, project)
        stranger = auth.register(db, "stranger@example.com", "correct-horse-battery")
        with pytest.raises(projects.ProjectNotFound):
            comparisons.list_versions(db, stranger, project.id)


class TestDiff:
    def test_identical_runs_report_nothing_material(
        self, db: Session, user: User, project
    ) -> None:
        change = comparisons.diff(run(db, user, project), run(db, user, project))
        assert change.is_material is False
        assert change.moved == []

    def test_quantity_change_moves_the_per_unit_cost(
        self, db: Session, user: User, project
    ) -> None:
        """Tooling amortization: the same quote costs less per unit at volume."""
        small = run(db, user, project, quantity=1_000)
        large = run(db, user, project, quantity=100_000)
        change = comparisons.diff(small, large)
        supplier = next(c for c in change.changes if c.supplier_id == "A")
        assert supplier.delta < 0
        assert supplier.delta_percent < 0

    def test_versions_are_labelled(self, db: Session, user: User, project) -> None:
        change = comparisons.diff(run(db, user, project), run(db, user, project))
        assert (change.from_version, change.to_version) == (1, 2)

    def test_a_supplier_becoming_comparable_is_reported(
        self, db: Session, user: User, project
    ) -> None:
        """The change a buyer is waiting for: blocked before, costed now."""
        before = run(db, user, project)
        result = result_for(before, "C")
        result.state = "landed"
        result.breakdown = {"per_unit": {"value": "12.00"}}
        result.refusal = None
        db.commit()

        change = comparisons.diff(run(db, user, project), before)
        supplier = next(c for c in change.changes if c.supplier_id == "C")
        assert supplier.became_comparable
        assert not supplier.became_blocked

    def test_recommendation_flip_is_detected(self, db: Session, user: User, project) -> None:
        first = run(db, user, project)
        second = run(db, user, project)
        cheapest = result_for(second, "A")
        cheapest.breakdown = {**cheapest.breakdown, "per_unit": {"value": "0.01"}}
        db.commit()

        change = comparisons.diff(first, second)
        assert change.current_recommendation == "A"
        assert change.is_material

    def test_resolved_conflicts_are_listed(self, db: Session, user: User, project) -> None:
        before = run(db, user, project)
        after = run(db, user, project)
        result_for(after, "B").conflicts = []
        db.commit()

        change = comparisons.diff(before, after)
        supplier = next(c for c in change.changes if c.supplier_id == "B")
        assert any("minimum order quantity" in m for m in supplier.resolved_conflicts)
        assert supplier.new_conflicts == []

    def test_a_supplier_disappearing_is_not_passed_over_silently(
        self, db: Session, user: User, project
    ) -> None:
        before = run(db, user, project)
        after = run(db, user, project)
        db.delete(result_for(after, "C"))
        db.commit()
        db.refresh(after)

        change = comparisons.diff(before, after)
        supplier = next(c for c in change.changes if c.supplier_id == "C")
        assert supplier.is_gone
        assert supplier.has_moved

    def test_per_unit_survives_the_json_round_trip_as_a_decimal(
        self, db: Session, user: User, project
    ) -> None:
        """Stored as a string so no float ever touches money."""
        change = comparisons.diff(run(db, user, project), run(db, user, project))
        supplier = next(c for c in change.changes if c.supplier_id == "A")
        assert isinstance(supplier.current_per_unit, Decimal)
