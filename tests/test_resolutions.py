"""Resolution tests.

The demo sequence this makes possible: a quote is refused for a missing freight
rate, a buyer supplies it, the total appears — carrying a visible mark that the
number came from a person rather than a document.

That last part is the whole reason this is safe to offer, so most of these assertions
are about the mark rather than the arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from landed.core.cost_engine import compute
from landed.core.pipeline import compare_pack
from landed.core.resolutions import HumanResolution, apply
from landed.core.schema import (
    Attribution,
    Conflict,
    ConflictKind,
    CostAssumptions,
    Count,
    Incoterm,
    LineItem,
    Money,
    Origin,
    PriceBasis,
    Quotation,
    QuoteState,
    Source,
    Sourced,
    Text,
)
from landed.db.models import User
from landed.services import projects, resolutions
from tests.test_pipeline import PACK, PackStub

SRC = Source(file="quote_c.pdf", page=1)
BUYER = Attribution(
    actor="buyer@example.com",
    at=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
    rationale="confirmed with supplier by email",
)


def blocked_quote() -> Quotation:
    """Supplier C: priced, but with no delivery terms stated."""
    return Quotation(
        supplier_id="C",
        currency=Text(value="USD", source=SRC),
        line_items=[
            LineItem(
                unit_price=Money(value=Decimal("11.20"), currency="USD", source=SRC),
                price_basis=Sourced[PriceBasis](value=PriceBasis.PER_PIECE, source=SRC),
            )
        ],
        missing=["incoterm"],
    )


def assumptions(**overrides) -> CostAssumptions:
    defaults = dict(
        base_currency="USD",
        freight_flat=Money(value=Decimal("8200"), currency="USD", source=SRC),
        duty_rate=Sourced[Decimal](value=Decimal("0.065"), source=SRC),
    )
    return CostAssumptions(**{**defaults, **overrides})


def supplied(field_path: str, value: str, **extra) -> HumanResolution:
    return HumanResolution(
        supplier_id="C", field_path=field_path, value=value, attribution=BUYER, **extra
    )


class TestSuppliedValues:
    def test_a_supplied_incoterm_unblocks_the_quote(self) -> None:
        quote, _ = apply(blocked_quote(), assumptions(), [supplied("incoterm", "FOB")])
        assert quote.incoterm.value is Incoterm.FOB
        assert quote.missing == []
        assert quote.state is QuoteState.LANDED

    def test_the_supplied_value_is_marked_as_a_human_assumption(self) -> None:
        """Never disguised as evidence — this is what makes the feature safe."""
        quote, _ = apply(blocked_quote(), assumptions(), [supplied("incoterm", "FOB")])
        assert quote.incoterm.origin is Origin.ASSUMED
        assert quote.incoterm.source is None
        assert quote.incoterm.attribution.actor == "buyer@example.com"

    def test_a_supplied_freight_rate_reaches_the_cost_engine(self) -> None:
        _, updated = apply(
            blocked_quote(), assumptions(freight_flat=None), [supplied("freight", "2400")]
        )
        assert updated.freight_flat.value == Decimal("2400")
        assert updated.freight_flat.origin is Origin.ASSUMED
        assert "not stated in any document" in updated.freight_flat.note

    def test_a_supplied_total_actually_computes(self) -> None:
        quote, updated = apply(
            blocked_quote(),
            assumptions(freight_flat=None),
            [supplied("incoterm", "FOB"), supplied("freight", "2400")],
        )
        result = compute(quote, 10_000, updated)
        assert result.total.value > 0
        assert result.freight.value == Decimal("2400")

    def test_nonsense_leaves_the_quote_blocked(self) -> None:
        """A wrong value is worse than a known gap, so unparseable input changes
        nothing."""
        quote, _ = apply(
            blocked_quote(), assumptions(), [supplied("incoterm", "whenever they like")]
        )
        assert quote.incoterm is None
        assert quote.state is QuoteState.NOT_LANDED

    def test_resolutions_for_other_suppliers_are_ignored(self) -> None:
        other = HumanResolution(
            supplier_id="A", field_path="incoterm", value="FOB", attribution=BUYER
        )
        quote, _ = apply(blocked_quote(), assumptions(), [other])
        assert quote.incoterm is None

    def test_originals_are_left_intact(self) -> None:
        original = blocked_quote()
        apply(original, assumptions(), [supplied("incoterm", "FOB")])
        assert original.incoterm is None
        assert original.missing == ["incoterm"]


class TestSourceChoices:
    def contested(self) -> Quotation:
        """A priced quote whose MOQ the profile disputes.

        The line item matters: a chosen value with nowhere to land must not be
        treated as having settled anything, so a fixture without one would be
        testing the wrong path.
        """
        return Quotation(
            supplier_id="C",
            line_items=[
                LineItem(
                    unit_price=Money(value=Decimal("11.20"), currency="USD", source=SRC),
                    price_basis=Sourced[PriceBasis](
                        value=PriceBasis.PER_PIECE, source=SRC
                    ),
                    moq=Count(value=5_000, source=SRC),
                )
            ],
            conflicts=[
                Conflict(
                    kind=ConflictKind.CONTRADICTION,
                    field_path="moq",
                    message="minimum order quantity disagrees",
                    values=["5000", "10000"],
                )
            ],
        )

    def test_the_chosen_value_replaces_the_disputed_one(self) -> None:
        choice = supplied("moq", "10000", chosen_file="profile_c.pdf")
        quote, _ = apply(self.contested(), assumptions(), [choice])
        assert quote.line_items[0].moq.value == 10_000
        assert quote.line_items[0].moq.origin is Origin.ASSUMED

    def test_a_choice_that_cannot_be_applied_settles_nothing(self) -> None:
        """Otherwise the dispute closes while costing still uses the rejected value."""
        bare = Quotation(
            supplier_id="C",
            conflicts=[
                Conflict(
                    kind=ConflictKind.CONTRADICTION,
                    field_path="moq",
                    message="minimum order quantity disagrees",
                    values=["5000", "10000"],
                )
            ],
        )
        quote, _ = apply(
            bare, assumptions(), [supplied("moq", "10000", chosen_file="profile_c.pdf")]
        )
        assert quote.open_conflicts

    def test_choosing_a_source_closes_the_conflict(self) -> None:
        choice = supplied("moq", "10000", chosen_file="profile_c.pdf")
        quote, _ = apply(self.contested(), assumptions(), [choice])
        assert quote.state is QuoteState.LANDED
        assert quote.open_conflicts == []

    def test_the_losing_value_is_kept(self) -> None:
        """The record of what was in dispute survives the decision."""
        choice = supplied("moq", "10000", chosen_file="profile_c.pdf")
        quote, _ = apply(self.contested(), assumptions(), [choice])
        assert set(quote.conflicts[0].values) == {"5000", "10000"}
        assert quote.conflicts[0].resolved_with.actor == "buyer@example.com"

    def test_the_chosen_document_is_named(self) -> None:
        _, updated = apply(
            blocked_quote(),
            assumptions(freight_flat=None),
            [supplied("freight", "2400", chosen_file="forwarder_quote.pdf")],
        )
        assert "chose the value stated in forwarder_quote.pdf" in updated.freight_flat.note


class TestPersistence:
    @pytest.fixture(autouse=True)
    def upload_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(
            projects, "settings", lambda: SimpleNamespace(upload_dir=tmp_path)
        )
        return tmp_path

    @pytest.fixture
    def project(self, db: Session, user: User):
        return projects.create_project(db, user, "Q3 Enclosure Sourcing")

    def test_a_supplied_value_records_its_author(
        self, db: Session, user: User, project
    ) -> None:
        stored = resolutions.supply_value(
            db, user, project.id, "C", "freight", "2400", currency="USD"
        )
        assert stored.actor_email == "buyer@example.com"
        assert stored.is_active

    def test_reverting_keeps_the_record(self, db: Session, user: User, project) -> None:
        """A report issued under an assumption stays explainable after withdrawal."""
        stored = resolutions.supply_value(db, user, project.id, "C", "freight", "2400")
        resolutions.revert(db, user, project.id, stored.id)
        assert not stored.is_active
        assert len(resolutions.history(db, user, project.id)) == 1
        assert resolutions.active(db, user, project.id) == []

    def test_a_later_value_supersedes_an_earlier_one(
        self, db: Session, user: User, project
    ) -> None:
        """Supplying twice is a correction, not a conflict."""
        resolutions.supply_value(db, user, project.id, "C", "freight", "2400")
        resolutions.supply_value(db, user, project.id, "C", "freight", "2650")
        standing = resolutions.active(db, user, project.id)
        assert len(standing) == 1
        assert standing[0].value == "2650"

    def test_active_returns_core_objects_the_pipeline_accepts(
        self, db: Session, user: User, project
    ) -> None:
        resolutions.supply_value(db, user, project.id, "C", "incoterm", "FOB")
        standing = resolutions.active(db, user, project.id)
        assert isinstance(standing[0], HumanResolution)
        assert standing[0].attribution.actor == "buyer@example.com"

    def test_another_user_cannot_record_against_your_project(
        self, db: Session, user: User, project
    ) -> None:
        from landed.services import auth

        stranger = auth.register(db, "stranger@example.com", "correct-horse-battery")
        with pytest.raises(projects.ProjectNotFound):
            resolutions.supply_value(db, stranger, project.id, "C", "freight", "1")


class TestThroughThePipeline:
    def test_supplying_the_missing_term_lands_the_supplier(self) -> None:
        """End to end: supplier C is refused, then supplied, then costed."""
        before = compare_pack(PACK, 10_000, provider=PackStub())
        blocked = next(s for s in before.suppliers if s.supplier_id == "C")
        assert blocked.state is QuoteState.NOT_LANDED

        after = compare_pack(
            PACK,
            10_000,
            provider=PackStub(),
            resolutions=[
                HumanResolution(
                    supplier_id="C",
                    field_path="incoterm",
                    value="FOB Ningbo",
                    attribution=BUYER,
                )
            ],
        )
        unblocked = next(s for s in after.suppliers if s.supplier_id == "C")
        assert unblocked.state is QuoteState.LANDED
        assert unblocked.breakdown.total.value > 0

    def test_the_assumption_is_visible_on_the_costed_supplier(self) -> None:
        after = compare_pack(
            PACK,
            10_000,
            provider=PackStub(),
            resolutions=[
                HumanResolution(
                    supplier_id="C", field_path="incoterm", value="FOB", attribution=BUYER
                )
            ],
        )
        supplier = next(s for s in after.suppliers if s.supplier_id == "C")
        assert supplier.quotation.incoterm.origin is Origin.ASSUMED
