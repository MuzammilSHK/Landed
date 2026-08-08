"""Cost engine tests.

This is the module that must be provably correct — it produces the number judges
check against the organizer's reference calculations. Tests here are worth more than
tests anywhere else in the project.
"""

from __future__ import annotations

from decimal import Decimal

from landed.core.cost_engine import break_even, compute
from landed.core.schema import (
    CostAssumptions,
    CostBreakdown,
    Incoterm,
    LineItem,
    Money,
    PriceBasis,
    Quotation,
    Refusal,
    Source,
    Sourced,
)

SRC = Source(file="quote_a.pdf", page=2)


def m(amount: str, currency: str = "USD") -> Money:
    return Money(value=Decimal(amount), currency=currency, source=SRC)


def rate(value: str) -> Sourced[Decimal]:
    return Sourced[Decimal](value=Decimal(value), source=SRC)


def item(
    price: str = "12.40",
    basis: PriceBasis = PriceBasis.PER_PIECE,
    tooling: str | None = "8400",
    weight: str | None = None,
    currency: str = "USD",
) -> LineItem:
    return LineItem(
        unit_price=m(price, currency),
        price_basis=Sourced[PriceBasis](value=basis, source=SRC),
        tooling_cost=m(tooling, currency) if tooling else None,
        unit_weight_kg=m(weight) if weight else None,
    )


def quote(
    incoterm: Incoterm | None = Incoterm.FOB,
    currency: str = "USD",
    items: list[LineItem] | None = None,
) -> Quotation:
    return Quotation(
        supplier_id="A",
        currency=Sourced[str](value=currency, source=SRC) if currency else None,
        incoterm=Sourced[Incoterm](value=incoterm, source=SRC) if incoterm else None,
        line_items=items if items is not None else [item()],
    )


def assumptions(**overrides) -> CostAssumptions:
    defaults = dict(
        base_currency="USD",
        freight_flat=m("8200"),
        duty_rate=rate("0.065"),
        insurance_rate=rate("0.005"),
        financing_annual_rate=rate("0.08"),
        payment_days_outstanding=60,
    )
    return CostAssumptions(**{**defaults, **overrides})


def landed(result: object) -> CostBreakdown:
    assert isinstance(result, CostBreakdown), f"expected a total, got {result}"
    return result


class TestRefusalGuard:
    """The fallback case the brief requires demonstrated."""

    def test_missing_incoterm_refuses(self) -> None:
        result = compute(quote(incoterm=None), 10_000, assumptions())
        assert isinstance(result, Refusal)
        assert "incoterm" in result.missing_fields

    def test_missing_freight_rate_refuses_when_buyer_bears_freight(self) -> None:
        """FOB puts main carriage on the buyer, so an unstated rate blocks the total."""
        result = compute(quote(), 10_000, assumptions(freight_flat=None))
        assert isinstance(result, Refusal)
        assert "freight" in result.missing_fields

    def test_undated_fx_refuses_rather_than_picking_a_rate(self) -> None:
        result = compute(
            quote(currency="EUR", items=[item(currency="EUR")]),
            10_000,
            assumptions(fx_rate_to_base=rate("1.08"), fx_rate_date=None),
        )
        assert isinstance(result, Refusal)
        assert "fx_rate" in result.missing_fields

    def test_refusal_names_every_missing_field_at_once(self) -> None:
        """The user must learn the full set in one pass, not one gap at a time."""
        bare = Quotation(supplier_id="C")
        result = compute(bare, 10_000, assumptions(freight_flat=None, duty_rate=None))
        assert isinstance(result, Refusal)
        assert {"unit_price", "price_basis", "currency", "incoterm"} <= set(
            result.missing_fields
        )

    def test_non_positive_quantity_refuses(self) -> None:
        assert isinstance(compute(quote(), 0, assumptions()), Refusal)

    def test_per_kg_price_without_weight_refuses(self) -> None:
        result = compute(
            quote(items=[item(price="4.20", basis=PriceBasis.PER_KG, weight=None)]),
            10_000,
            assumptions(),
        )
        assert isinstance(result, Refusal)


class TestKnownTotal:
    """One fully worked example, checked to the cent."""

    def test_fob_total_and_per_unit(self) -> None:
        result = landed(compute(quote(), 10_000, assumptions()))
        assert result.goods.value == Decimal("124000.00")
        assert result.freight.value == Decimal("8200")
        assert result.insurance.value == Decimal("661.000")
        assert result.tooling_amortized.value == Decimal("8400")
        assert result.total.value == Decimal("151527.65")
        assert result.per_unit.value == Decimal("15.15")

    def test_duty_is_assessed_on_cif_value(self) -> None:
        result = landed(compute(quote(), 10_000, assumptions()))
        cif = result.goods.value + result.freight.value + result.insurance.value
        assert result.duty.value == cif * Decimal("0.065")

    def test_every_term_is_marked_derived(self) -> None:
        """A computed value must never look like one read from a document."""
        result = landed(compute(quote(), 10_000, assumptions()))
        for term in (result.goods, result.freight, result.duty, result.total):
            assert term.origin.value == "derived"
            assert term.note


class TestPriceBasis:
    def test_per_1000_matches_the_equivalent_per_piece_price(self) -> None:
        """$12,400 per 1000 is $12.40 per piece — the 1000x error, caught."""
        per_1000 = compute(
            quote(items=[item(price="12400", basis=PriceBasis.PER_1000)]),
            10_000,
            assumptions(),
        )
        per_piece = compute(quote(), 10_000, assumptions())
        assert landed(per_1000).goods.value == landed(per_piece).goods.value

    def test_lot_price_spreads_over_quantity(self) -> None:
        result = landed(
            compute(
                quote(items=[item(price="124000", basis=PriceBasis.LOT, tooling=None)]),
                10_000,
                assumptions(),
            )
        )
        assert result.goods.value == Decimal("124000.00")


class TestIncotermResponsibility:
    def test_ddp_puts_duty_on_the_seller(self) -> None:
        result = landed(compute(quote(incoterm=Incoterm.DDP), 10_000, assumptions()))
        assert result.duty.value == 0
        assert "seller bears import duty" in result.duty.note

    def test_cif_puts_freight_and_insurance_on_the_seller(self) -> None:
        result = landed(compute(quote(incoterm=Incoterm.CIF), 10_000, assumptions()))
        assert result.freight.value == 0
        assert result.insurance.value == 0
        assert result.duty.value > 0

    def test_exw_puts_everything_on_the_buyer(self) -> None:
        result = landed(compute(quote(incoterm=Incoterm.EXW), 10_000, assumptions()))
        assert result.freight.value > 0
        assert result.insurance.value > 0
        assert result.duty.value > 0

    def test_incoterm_changes_the_total(self) -> None:
        """Identical unit prices under different terms must not cost the same."""
        fob = landed(compute(quote(incoterm=Incoterm.FOB), 10_000, assumptions()))
        ddp = landed(compute(quote(incoterm=Incoterm.DDP), 10_000, assumptions()))
        assert fob.total.value != ddp.total.value


class TestTooling:
    def test_per_unit_cost_falls_as_quantity_rises(self) -> None:
        small = landed(compute(quote(), 1_000, assumptions()))
        large = landed(compute(quote(), 100_000, assumptions()))
        assert large.per_unit.value < small.per_unit.value

    def test_high_tooling_supplier_wins_only_at_volume(self) -> None:
        cheap_tooling = quote(items=[item(price="13.10", tooling="500")])
        heavy_tooling = quote(items=[item(price="12.40", tooling="40000")])
        a = assumptions()
        assert landed(compute(heavy_tooling, 1_000, a)).per_unit.value > landed(
            compute(cheap_tooling, 1_000, a)
        ).per_unit.value
        assert landed(compute(heavy_tooling, 200_000, a)).per_unit.value < landed(
            compute(cheap_tooling, 200_000, a)
        ).per_unit.value


class TestOptionalTerms:
    def test_absent_insurance_rate_degrades_to_zero_with_a_note(self) -> None:
        """A refinement missing should not block an otherwise sound comparison."""
        result = landed(compute(quote(), 10_000, assumptions(insurance_rate=None)))
        assert result.insurance.value == 0
        assert "not modelled" in result.insurance.note

    def test_absent_financing_rate_degrades_to_zero(self) -> None:
        result = landed(compute(quote(), 10_000, assumptions(financing_annual_rate=None)))
        assert result.financing.value == 0


class TestFreightPerKg:
    def test_weight_based_freight_scales_with_quantity(self) -> None:
        a = assumptions(freight_flat=None, freight_per_kg=m("2.50"))
        q = quote(items=[item(weight="0.4")])
        assert landed(compute(q, 10_000, a)).freight.value == Decimal("10000.0")
        assert landed(compute(q, 20_000, a)).freight.value == Decimal("20000.0")


class TestBreakEven:
    def test_sweep_returns_a_value_per_quantity(self) -> None:
        sweep = break_even([quote()], assumptions(), [1_000, 10_000, 100_000])
        assert list(sweep["A"]) == [1_000, 10_000, 100_000]
        assert all(v is not None for v in sweep["A"].values())

    def test_uncostable_supplier_shows_gaps_not_zeros(self) -> None:
        """A supplier we declined to cost must not read as free."""
        sweep = break_even([quote(incoterm=None)], assumptions(), [1_000, 10_000])
        assert all(v is None for v in sweep["A"].values())


class TestDeterminism:
    def test_identical_input_gives_identical_output(self) -> None:
        a, b = compute(quote(), 10_000, assumptions()), compute(
            quote(), 10_000, assumptions()
        )
        assert landed(a).total.value == landed(b).total.value
