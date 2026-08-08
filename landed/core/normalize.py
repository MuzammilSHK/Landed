"""Make quotes comparable, or decline to.

The failure this module exists to prevent: a price quoted per thousand sitting in
the same column as one quoted per piece, with the comparison looking entirely
reasonable. That error is invisible in a spreadsheet and expensive in production.

Conversions return `Origin.DERIVED` values carrying a note naming what was applied,
so a converted number never masquerades as one read from a document. Where a
conversion cannot be made honestly — a foreign currency with no rate date — these
functions return None rather than choosing a plausible input. `conflicts` turns
those Nones into something the user can act on.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from .schema import CostAssumptions, Incoterm, Money, Origin, PriceBasis, Sourced

CENTS = Decimal("0.01")

_INCOTERM_TOKEN = re.compile(r"\b([A-Z]{3})\b")

_BASIS_PATTERNS: tuple[tuple[re.Pattern[str], PriceBasis], ...] = (
    (re.compile(r"per\s*(1[,.]?000|thousand|k\b)|/\s*m\b|per\s*m\b"), PriceBasis.PER_1000),
    (re.compile(r"per\s*kg|/\s*kg|per\s*kilo"), PriceBasis.PER_KG),
    (re.compile(r"\blot\b|lump\s*sum|per\s*order|\bflat\b"), PriceBasis.LOT),
    (re.compile(r"per\s*(piece|pc|pcs|unit|ea\b)|\beach\b|\bea\b"), PriceBasis.PER_PIECE),
)


def money(amount: Decimal) -> Decimal:
    """Quantize to cents. Applied at totals only — never mid-calculation."""
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def parse_incoterm(text: str | None) -> Incoterm | None:
    """Pull an Incoterm out of free text such as "FOB Shanghai" or "F.O.B."."""
    if not text:
        return None
    cleaned = re.sub(r"[^A-Za-z ]", "", text).upper()
    valid = {term.value for term in Incoterm}
    for token in _INCOTERM_TOKEN.findall(cleaned):
        if token in valid:
            return Incoterm(token)
    return None


def parse_price_basis(text: str | None) -> PriceBasis | None:
    """Read a pricing basis from free text.

    Order matters: "per 1000 pieces" is per-thousand, so the thousand patterns are
    tested before the per-piece ones.
    """
    if not text:
        return None
    lowered = text.lower()
    for pattern, basis in _BASIS_PATTERNS:
        if pattern.search(lowered):
            return basis
    return None


def to_per_piece(
    price: Money,
    basis: PriceBasis,
    quantity: int,
    unit_weight_kg: Decimal | None = None,
) -> Money | None:
    """Restate a price as cost per piece.

    Returns None when the conversion needs data the quote does not provide — a
    per-kg price without a unit weight, or a lot price without a quantity.
    """
    if basis is PriceBasis.PER_PIECE:
        return price
    if basis is PriceBasis.PER_1000:
        return _derived(price, price.value / Decimal(1000), "restated from per-1000")
    if basis is PriceBasis.PER_KG:
        if not unit_weight_kg:
            return None
        return _derived(price, price.value * unit_weight_kg, "restated from per-kg")
    if quantity <= 0:
        return None
    return _derived(price, price.value / Decimal(quantity), "lot price spread over quantity")


def convert_to_base(amount: Money, assumptions: CostAssumptions) -> Money | None:
    """Convert to the base currency at a dated rate.

    Returns None when the currency differs and no dated rate is available. Falling
    back to a current or average rate would fabricate the number this whole system
    exists to keep honest.
    """
    if amount.currency == assumptions.base_currency:
        return amount
    rate, rate_date = assumptions.fx_rate_to_base, assumptions.fx_rate_date
    if rate is None or not rate_date:
        return None
    converted = _derived(
        amount,
        amount.value * rate.value,
        f"converted from {amount.currency} at {rate.value} on {rate_date}",
    )
    return converted.model_copy(update={"currency": assumptions.base_currency})


def _derived(basis: Sourced, value: Decimal, note: str) -> Money:
    """A computed value that keeps a pointer to what it was computed from."""
    return Money(
        value=value,
        unit=basis.unit,
        currency=basis.currency,
        origin=Origin.DERIVED,
        source=basis.source,
        attribution=basis.attribution,
        confidence=basis.confidence,
        note=note,
    )
