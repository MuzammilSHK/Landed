"""Conflict detector tests.

Each detector is checked in both directions: it fires when it should and stays quiet
when it shouldn't. False conflicts are as damaging as missed ones — a panel crying
wolf over clean quotes destroys the product's credibility in a live demo.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from landed.core.conflicts import (
    annotate,
    detect_ambiguous_basis,
    detect_contradictions,
    detect_missing,
    detect_price_outlier,
    detect_stale,
    detect_undated_currency,
    detect_vision_reads,
)
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
    ReadMethod,
    Source,
    Sourced,
    SupplierProfile,
    Text,
)

QUOTE_SRC = Source(file="quote_b.pdf", page=1)
PROFILE_SRC = Source(file="profile_b.pdf", page=4)
SCAN_SRC = Source(file="quote_c.pdf", page=2, read_method=ReadMethod.VISION)


def money(amount: str, currency: str = "USD", source: Source = QUOTE_SRC) -> Money:
    return Money(value=Decimal(amount), currency=currency, source=source)


def rate(value: str) -> Sourced[Decimal]:
    return Sourced[Decimal](value=Decimal(value), source=QUOTE_SRC)


def basis(
    value: PriceBasis = PriceBasis.PER_PIECE,
    *,
    confidence: float | None = None,
    origin: Origin = Origin.EXTRACTED,
) -> Sourced[PriceBasis]:
    return Sourced[PriceBasis](
        value=value, source=QUOTE_SRC, origin=origin, confidence=confidence
    )


def quote(
    *,
    price: str = "12.40",
    currency: str = "USD",
    incoterm: Incoterm | None = Incoterm.FOB,
    price_basis: Sourced[PriceBasis] | None = None,
    moq: int | None = None,
    price_source: Source = QUOTE_SRC,
    quote_date: str | None = None,
    validity_days: int | None = None,
) -> Quotation:
    return Quotation(
        supplier_id="B",
        currency=Text(value=currency, source=QUOTE_SRC),
        incoterm=(
            Sourced[Incoterm](value=incoterm, source=QUOTE_SRC) if incoterm else None
        ),
        quote_date=Text(value=quote_date, source=QUOTE_SRC) if quote_date else None,
        validity_days=(
            Count(value=validity_days, source=QUOTE_SRC) if validity_days else None
        ),
        line_items=[
            LineItem(
                unit_price=money(price, currency, price_source),
                price_basis=price_basis or basis(),
                moq=Count(value=moq, source=QUOTE_SRC) if moq else None,
            )
        ],
    )


def assumptions(**overrides) -> CostAssumptions:
    defaults = dict(
        base_currency="USD",
        freight_flat=money("8200"),
        duty_rate=rate("0.065"),
    )
    return CostAssumptions(**{**defaults, **overrides})


class TestMissing:
    def test_absent_required_field_is_flagged(self) -> None:
        found = detect_missing(quote(incoterm=None), 10_000, assumptions())
        assert any(c.field_path == "incoterm" for c in found)

    def test_message_is_plain_language(self) -> None:
        """'delivery terms (Incoterm)', not 'incoterm is None'."""
        found = detect_missing(quote(incoterm=None), 10_000, assumptions())
        message = next(c.message for c in found if c.field_path == "incoterm")
        assert message == "delivery terms (Incoterm) not stated"
        assert "None" not in message

    def test_complete_quote_produces_nothing(self) -> None:
        assert detect_missing(quote(), 10_000, assumptions()) == []


class TestContradictions:
    def test_moq_disagreement_between_quote_and_profile(self) -> None:
        """The canonical demo case: quotation says 5,000, profile says 10,000."""
        profile = SupplierProfile(
            supplier_id="B", moq=Count(value=10_000, source=PROFILE_SRC)
        )
        found = detect_contradictions(quote(moq=5_000), profile)
        assert len(found) == 1
        assert found[0].kind is ConflictKind.CONTRADICTION
        assert "5000" in found[0].message and "10000" in found[0].message

    def test_both_values_and_both_sources_are_retained(self) -> None:
        """Never collapse to one value — picking a winner is a fabricated fact."""
        profile = SupplierProfile(
            supplier_id="B", moq=Count(value=10_000, source=PROFILE_SRC)
        )
        conflict = detect_contradictions(quote(moq=5_000), profile)[0]
        assert set(conflict.values) == {"5000", "10000"}
        assert {s.file for s in conflict.sources} == {"quote_b.pdf", "profile_b.pdf"}

    def test_agreeing_sources_produce_no_conflict(self) -> None:
        profile = SupplierProfile(
            supplier_id="B", moq=Count(value=5_000, source=PROFILE_SRC)
        )
        assert detect_contradictions(quote(moq=5_000), profile) == []

    def test_profile_silent_on_a_field_is_not_a_disagreement(self) -> None:
        assert detect_contradictions(quote(moq=5_000), SupplierProfile(supplier_id="B")) == []


class TestUndatedCurrency:
    def test_foreign_currency_without_a_dated_rate_is_flagged(self) -> None:
        found = detect_undated_currency(quote(currency="EUR"), assumptions())
        assert len(found) == 1
        assert found[0].kind is ConflictKind.UNDATED_CURRENCY

    def test_no_rate_is_invented(self) -> None:
        """The system must not quietly pick a rate to keep the total tidy."""
        conflict = detect_undated_currency(quote(currency="EUR"), assumptions())[0]
        assert "supply the rate and its date" in conflict.message

    def test_base_currency_needs_no_rate(self) -> None:
        assert detect_undated_currency(quote(currency="USD"), assumptions()) == []

    def test_dated_rate_satisfies_the_check(self) -> None:
        dated = assumptions(fx_rate_to_base=rate("1.08"), fx_rate_date="2026-07-14")
        assert detect_undated_currency(quote(currency="EUR"), dated) == []


class TestAmbiguousBasis:
    def test_inferred_basis_is_flagged(self) -> None:
        found = detect_ambiguous_basis(quote(price_basis=basis(origin=Origin.MODEL)))
        assert len(found) == 1
        assert "1000x" in found[0].message

    def test_low_confidence_basis_is_flagged(self) -> None:
        found = detect_ambiguous_basis(quote(price_basis=basis(confidence=0.4)))
        assert len(found) == 1

    def test_clearly_stated_basis_is_not_flagged(self) -> None:
        """Normalization handles a declared per-1000 price correctly on its own."""
        stated = basis(PriceBasis.PER_1000, confidence=0.99)
        assert detect_ambiguous_basis(quote(price_basis=stated)) == []


class TestPriceOutlier:
    def test_thousandfold_price_is_flagged_against_peers(self) -> None:
        """Catches a basis misread that slipped through with high confidence."""
        peers = [quote(price="12.40"), quote(price="13.10")]
        found = detect_price_outlier(quote(price="12400"), peers, assumptions())
        assert len(found) == 1
        assert found[0].kind is ConflictKind.UNIT_MISMATCH

    def test_ordinary_price_spread_is_not_flagged(self) -> None:
        peers = [quote(price="12.40"), quote(price="13.10")]
        assert detect_price_outlier(quote(price="15.00"), peers, assumptions()) == []

    def test_no_peers_means_no_comparison(self) -> None:
        assert detect_price_outlier(quote(price="12400"), [], assumptions()) == []


class TestVisionReads:
    def test_scanned_value_is_flagged_but_does_not_block(self) -> None:
        found = detect_vision_reads(quote(price_source=SCAN_SRC))
        assert len(found) == 1
        assert found[0].blocks_total is False
        assert "page 2" in found[0].message

    def test_text_layer_value_is_not_flagged(self) -> None:
        assert detect_vision_reads(quote()) == []


class TestStale:
    def test_expired_quote_is_flagged_as_advisory(self) -> None:
        stale = quote(quote_date="2026-01-01", validity_days=30)
        found = detect_stale(stale, as_of=date(2026, 8, 8))
        assert len(found) == 1
        assert found[0].blocks_total is False

    def test_quote_inside_its_validity_window_is_fine(self) -> None:
        fresh = quote(quote_date="2026-08-01", validity_days=30)
        assert detect_stale(fresh, as_of=date(2026, 8, 8)) == []

    def test_unparseable_date_is_skipped_rather_than_guessed(self) -> None:
        odd = quote(quote_date="last Tuesday", validity_days=30)
        assert detect_stale(odd, as_of=date(2026, 8, 8)) == []


class TestAnnotate:
    def test_clean_quote_lands(self) -> None:
        assert annotate(quote(), 10_000, assumptions()).state is QuoteState.LANDED

    def test_contradiction_makes_a_quote_contested(self) -> None:
        profile = SupplierProfile(
            supplier_id="B", moq=Count(value=10_000, source=PROFILE_SRC)
        )
        annotated = annotate(quote(moq=5_000), 10_000, assumptions(), profile=profile)
        assert annotated.state is QuoteState.CONTESTED

    def test_missing_field_makes_a_quote_not_landed(self) -> None:
        annotated = annotate(quote(incoterm=None), 10_000, assumptions())
        assert annotated.state is QuoteState.NOT_LANDED
        assert "incoterm" in annotated.missing

    def test_advisory_only_quote_still_lands(self) -> None:
        annotated = annotate(quote(price_source=SCAN_SRC), 10_000, assumptions())
        assert annotated.state is QuoteState.LANDED
        assert annotated.conflicts  # surfaced, but not blocking

    def test_extra_conflicts_are_merged(self) -> None:
        """Injection attempts found during extraction arrive alongside the rest."""
        injection = Conflict(
            kind=ConflictKind.INJECTION_SUSPECTED,
            field_path="document",
            message="supplier profile contains instruction-shaped text",
        )
        annotated = annotate(quote(), 10_000, assumptions(), extra=[injection])
        assert any(
            c.kind is ConflictKind.INJECTION_SUSPECTED for c in annotated.conflicts
        )
        assert annotated.state is QuoteState.CONTESTED

    def test_annotate_does_not_mutate_the_input(self) -> None:
        original = quote(incoterm=None)
        annotate(original, 10_000, assumptions())
        assert original.missing == []
        assert original.conflicts == []

    def test_resolved_contradiction_lets_the_quote_land(self) -> None:
        resolved = Conflict(
            kind=ConflictKind.CONTRADICTION,
            field_path="moq",
            message="resolved by buyer",
            resolved_with=Attribution(actor="buyer@example.com", at=datetime.now()),
        )
        annotated = annotate(quote(), 10_000, assumptions(), extra=[resolved])
        assert annotated.state is QuoteState.LANDED
