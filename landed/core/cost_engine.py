"""Deterministic landed-cost computation.

No model client may be imported here. That split — a model reconciles messy
documents, arithmetic stays in code that can be unit-tested and diffed against
reference calculations — is the project's central guarantee, and
`tests/test_layering.py` enforces it.

The guard runs first and always. A missing required input returns a `Refusal`, never
a partial total and never a zero: a tidy number produced from an assumed freight
term is precisely the failure this project exists to prevent.

    goods      -> freight -> insurance -> duty -> financing -> total

Order matters. Duty is assessed on CIF value (goods + freight + insurance), which is
the basis most jurisdictions use, and the chain is stated on each returned term.

Required versus optional inputs is a deliberate line. Freight, duty, and FX
materially reorder suppliers, so their absence is a refusal. Insurance and financing
are refinements: absent, they degrade to zero carrying a note saying they were not
modelled, rather than blocking a comparison that is otherwise sound.
"""

from __future__ import annotations

from decimal import Decimal

from .normalize import convert_to_base, money, to_per_piece
from .schema import (
    REQUIRED_FOR_TOTAL,
    CostAssumptions,
    CostBreakdown,
    CostResult,
    Money,
    Origin,
    Quotation,
    Refusal,
)

ZERO = Decimal(0)


def compute(quote: Quotation, quantity: int, assumptions: CostAssumptions) -> CostResult:
    """Landed cost for one quotation at one order quantity.

    Returns `CostBreakdown` when the quote is complete, `Refusal` when it is not.
    Never raises on incomplete input — incompleteness is an expected state with a
    designed response.
    """
    if quantity <= 0:
        return Refusal(supplier_id=quote.supplier_id, reason="order quantity must be positive")

    missing = missing_inputs(quote, quantity, assumptions)
    if missing:
        return Refusal(
            supplier_id=quote.supplier_id,
            reason="cannot issue a total until these are supplied",
            missing_fields=missing,
            conflicts=quote.open_conflicts,
        )

    currency = assumptions.base_currency
    incoterm = quote.incoterm.value  # guarded above

    goods = _goods(quote, quantity, assumptions)
    freight = (
        _freight(quote, quantity, assumptions)
        if incoterm.buyer_bears_freight
        else _zero(currency, f"{incoterm.value}: seller bears freight")
    )
    insurance = (
        _insurance(goods.value + freight.value, assumptions)
        if incoterm.buyer_bears_insurance
        else _zero(currency, f"{incoterm.value}: seller bears insurance")
    )
    duty = (
        _duty(goods.value + freight.value + insurance.value, assumptions)
        if incoterm.buyer_bears_duty
        else _zero(currency, f"{incoterm.value}: seller bears import duty")
    )
    tooling = _tooling(quote, quantity, assumptions)
    financing = _financing(goods.value, assumptions)

    subtotal = (
        goods.value + tooling.value + freight.value
        + insurance.value + duty.value + financing.value
    )
    total = money(subtotal)

    return CostBreakdown(
        supplier_id=quote.supplier_id,
        quantity=quantity,
        currency=currency,
        goods=goods,
        tooling_amortized=tooling,
        freight=freight,
        insurance=insurance,
        duty=duty,
        financing=financing,
        total=_derived(total, currency, "sum of all terms"),
        per_unit=_derived(money(total / quantity), currency, "total divided by quantity"),
        assumptions=assumptions.all_sourced(),
    )


def missing_inputs(
    quote: Quotation, quantity: int, assumptions: CostAssumptions
) -> list[str]:
    """Every input absent that a total would require.

    Reported in one pass so the user learns the full set at once rather than
    discovering the next gap only after closing the first. Shared with
    `conflicts.detect_missing` so the two cannot drift apart.
    """
    missing = [name for name in REQUIRED_FOR_TOTAL if _quote_field(quote, name) is None]
    if quote.incoterm is None or quote.currency is None:
        return missing

    if _needs_fx(quote, assumptions):
        missing.append("fx_rate")

    incoterm = quote.incoterm.value
    if incoterm.buyer_bears_freight:
        if assumptions.freight_flat is None and assumptions.freight_per_kg is None:
            missing.append("freight")
        elif assumptions.freight_per_kg is not None and _total_weight(quote, 1) is None:
            missing.append("unit_weight_kg")
    if incoterm.buyer_bears_duty and assumptions.duty_rate is None:
        missing.append("duty_rate")
    if normalized_unit_price(quote, quantity, assumptions) is None:
        missing.append("unit_price_basis_conversion")
    return missing


# --------------------------------------------------------------------------- #
# Cost terms
# --------------------------------------------------------------------------- #

def _goods(quote: Quotation, quantity: int, assumptions: CostAssumptions) -> Money:
    per_unit = normalized_unit_price(quote, quantity, assumptions)
    assert per_unit is not None, "guarded by missing_inputs"
    return _derived(
        per_unit * quantity,
        assumptions.base_currency,
        f"{len(quote.line_items)} line item(s) at {per_unit} per unit x {quantity}",
    )


def normalized_unit_price(
    quote: Quotation, quantity: int, assumptions: CostAssumptions
) -> Decimal | None:
    """Sum of component prices for one finished unit, in base currency.

    None when any line item cannot be normalized — a per-kg price with no unit
    weight, or a currency with no dated rate. Public because `conflicts` compares
    it across suppliers to catch a pricing-basis misread.
    """
    total = ZERO
    for item in quote.line_items:
        if item.unit_price is None or item.price_basis is None:
            return None
        weight = item.unit_weight_kg.value if item.unit_weight_kg else None
        per_piece = to_per_piece(item.unit_price, item.price_basis.value, quantity, weight)
        if per_piece is None:
            return None
        in_base = convert_to_base(per_piece, assumptions)
        if in_base is None:
            return None
        total += in_base.value
    return total


def _tooling(quote: Quotation, quantity: int, assumptions: CostAssumptions) -> Money:
    """One-off tooling spread across the order.

    This is the term that flips rankings between volumes, and the reason the
    break-even chart is worth showing.
    """
    total = ZERO
    for item in quote.line_items:
        if item.tooling_cost is None:
            continue
        in_base = convert_to_base(item.tooling_cost, assumptions)
        if in_base is not None:
            total += in_base.value
    note = (
        f"{total} spread over {quantity} units" if total else "no tooling cost quoted"
    )
    return _derived(total, assumptions.base_currency, note)


def _freight(quote: Quotation, quantity: int, assumptions: CostAssumptions) -> Money:
    if assumptions.freight_flat is not None:
        return _derived(
            assumptions.freight_flat.value, assumptions.base_currency, "flat freight rate"
        )
    weight = _total_weight(quote, quantity)
    assert assumptions.freight_per_kg is not None and weight is not None
    return _derived(
        assumptions.freight_per_kg.value * weight,
        assumptions.base_currency,
        f"{weight} kg at {assumptions.freight_per_kg.value} per kg",
    )


def _insurance(insurable: Decimal, assumptions: CostAssumptions) -> Money:
    if assumptions.insurance_rate is None:
        return _zero(assumptions.base_currency, "insurance not modelled — no rate supplied")
    rate = assumptions.insurance_rate.value
    return _derived(
        insurable * rate,
        assumptions.base_currency,
        f"{rate} of goods plus freight",
    )


def _duty(cif_value: Decimal, assumptions: CostAssumptions) -> Money:
    assert assumptions.duty_rate is not None, "guarded by missing_inputs"
    rate = assumptions.duty_rate.value
    return _derived(
        cif_value * rate,
        assumptions.base_currency,
        f"{rate} of CIF value (goods plus freight plus insurance)",
    )


def _financing(goods_value: Decimal, assumptions: CostAssumptions) -> Money:
    """Working capital tied up by the payment terms.

    30% advance against net-60 is real money the headline unit price never shows.
    """
    rate = assumptions.financing_annual_rate
    days = assumptions.payment_days_outstanding
    if rate is None or days <= 0:
        return _zero(assumptions.base_currency, "financing not modelled")
    cost = goods_value * rate.value * Decimal(days) / Decimal(365)
    return _derived(
        cost,
        assumptions.base_currency,
        f"{rate.value} annual over {days} days outstanding",
    )


# --------------------------------------------------------------------------- #
# Scenario sweep
# --------------------------------------------------------------------------- #

def break_even(
    quotes: list[Quotation], assumptions: CostAssumptions, quantities: list[int]
) -> dict[str, dict[int, Decimal | None]]:
    """Per-unit landed cost across a quantity sweep, per supplier.

    None marks a quantity at which a supplier cannot be costed, so the chart shows
    gaps rather than implying a number we declined to produce.
    """
    sweep: dict[str, dict[int, Decimal | None]] = {}
    for quote in quotes:
        row: dict[int, Decimal | None] = {}
        for quantity in quantities:
            result = compute(quote, quantity, assumptions)
            row[quantity] = (
                result.per_unit.value if isinstance(result, CostBreakdown) else None
            )
        sweep[quote.supplier_id] = row
    return sweep


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _quote_field(quote: Quotation, name: str) -> object | None:
    """Look up a required field on the quote or on its first line item."""
    if hasattr(quote, name):
        return getattr(quote, name)
    return next(
        (getattr(item, name) for item in quote.line_items if getattr(item, name) is not None),
        None,
    )


def _needs_fx(quote: Quotation, assumptions: CostAssumptions) -> bool:
    if quote.currency is None or quote.currency.value == assumptions.base_currency:
        return False
    return assumptions.fx_rate_to_base is None or not assumptions.fx_rate_date


def _total_weight(quote: Quotation, quantity: int) -> Decimal | None:
    weights = [i.unit_weight_kg.value for i in quote.line_items if i.unit_weight_kg]
    if not weights:
        return None
    return sum(weights, ZERO) * quantity


def _derived(value: Decimal, currency: str, note: str) -> Money:
    return Money(value=value, currency=currency, origin=Origin.DERIVED, note=note)


def _zero(currency: str, note: str) -> Money:
    return _derived(ZERO, currency, note)
