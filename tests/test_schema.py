"""Schema contract tests.

The provenance guarantee has to hold structurally. If a bare number can reach the
cost engine, "every value carries its source" is a claim made in the pitch rather
than a property of the system.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from landed.core.schema import (
    Attribution,
    Conflict,
    ConflictKind,
    Money,
    Origin,
    Quotation,
    QuoteState,
    ReadMethod,
    Source,
)


def doc(page: int = 1, method: ReadMethod = ReadMethod.TEXT_LAYER) -> Source:
    return Source(file="quote_a.pdf", page=page, read_method=method)


def blocking_conflict(**kwargs) -> Conflict:
    defaults = dict(
        kind=ConflictKind.CONTRADICTION,
        field_path="line_items[0].moq",
        message="MOQ disagrees across sources",
        blocks_total=True,
    )
    return Conflict(**{**defaults, **kwargs})


class TestProvenance:
    def test_extracted_value_requires_a_source(self) -> None:
        with pytest.raises(ValidationError, match="extracted values require a source"):
            Money(value=Decimal("12.40"), currency="USD")

    def test_extracted_value_with_a_source_is_accepted(self) -> None:
        price = Money(value=Decimal("12.40"), currency="USD", source=doc(page=2))
        assert price.source is not None
        assert price.source.page == 2

    def test_assumed_value_requires_attribution_or_source(self) -> None:
        with pytest.raises(ValidationError, match="assumed values require"):
            Money(value=Decimal("2400"), currency="USD", origin=Origin.ASSUMED)

    def test_assumed_value_accepts_a_human_attribution(self) -> None:
        """A value supplied in chat is legitimate — but it must name who supplied it."""
        supplied = Money(
            value=Decimal("2400"),
            currency="USD",
            origin=Origin.ASSUMED,
            attribution=Attribution(
                actor="buyer@example.com",
                at=datetime(2026, 8, 8, 14, 30),
                rationale="confirmed with supplier by email",
            ),
        )
        assert supplied.attribution is not None
        assert supplied.attribution.actor == "buyer@example.com"

    def test_derived_value_needs_neither(self) -> None:
        """Computed values are traceable through their inputs, not a document."""
        total = Money(value=Decimal("139500"), currency="USD", origin=Origin.DERIVED)
        assert total.source is None


class TestVisionReads:
    def test_vision_read_is_flagged_for_verification(self) -> None:
        price = Money(
            value=Decimal("12.40"),
            currency="USD",
            source=doc(method=ReadMethod.VISION),
        )
        assert price.needs_verification is True

    def test_text_layer_read_needs_no_verification(self) -> None:
        price = Money(value=Decimal("12.40"), currency="USD", source=doc())
        assert price.needs_verification is False

    def test_derived_value_is_not_a_verification_candidate(self) -> None:
        total = Money(value=Decimal("1"), currency="USD", origin=Origin.DERIVED)
        assert total.needs_verification is False


class TestState:
    def test_missing_required_field_yields_not_landed(self) -> None:
        quote = Quotation(supplier_id="C", missing=["incoterm"])
        assert quote.state is QuoteState.NOT_LANDED

    def test_blocking_conflict_yields_contested(self) -> None:
        quote = Quotation(supplier_id="B", conflicts=[blocking_conflict()])
        assert quote.state is QuoteState.CONTESTED

    def test_clean_quote_yields_landed(self) -> None:
        assert Quotation(supplier_id="A").state is QuoteState.LANDED

    def test_advisory_conflict_does_not_block(self) -> None:
        """A vision-read notice is worth surfacing but must not withhold a total."""
        advisory = Conflict(
            kind=ConflictKind.VISION_READ,
            field_path="line_items[0].unit_price",
            message="read from a scanned image — verify",
            blocks_total=False,
        )
        assert Quotation(supplier_id="A", conflicts=[advisory]).state is QuoteState.LANDED

    def test_resolved_conflict_stops_blocking(self) -> None:
        """Once a human picks the authoritative source, the quote can land."""
        resolved = blocking_conflict(
            resolved_with=Attribution(actor="buyer@example.com", at=datetime.now())
        )
        quote = Quotation(supplier_id="B", conflicts=[resolved])
        assert quote.state is QuoteState.LANDED
        assert quote.open_conflicts == []

    def test_missing_field_outranks_a_resolved_conflict(self) -> None:
        quote = Quotation(
            supplier_id="C",
            missing=["incoterm"],
            conflicts=[
                blocking_conflict(
                    resolved_with=Attribution(actor="buyer@example.com", at=datetime.now())
                )
            ],
        )
        assert quote.state is QuoteState.NOT_LANDED
