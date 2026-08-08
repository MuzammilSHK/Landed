"""Make quotes comparable, or refuse to.

The failure this module exists to prevent: a quote priced per thousand sitting in
the same column as one priced per piece, and the comparison looking fine. That
error is invisible in a spreadsheet and expensive in production.

Every conversion performed here records itself as an assumption on the resulting
Field, so the UI can show *what was changed and why* rather than presenting a
converted number as if it had been read that way.

Where a conversion cannot be made safely — currency with no date to convert at —
this module does not pick a rate. It defers to `conflicts.py`.
"""

from __future__ import annotations

from decimal import Decimal

from .schema import Field, PriceBasis, Quotation


def normalize_quotation(quote: Quotation) -> Quotation:
    """Bring one quote to canonical units. Returns a new Quotation; the original
    is preserved so the UI can show before/after.

    TODO
    """
    raise NotImplementedError


def to_per_piece(price: Field, basis: PriceBasis, quantity: int) -> Field:
    """Convert any price basis to per-piece.

    Result carries origin=DERIVED and a note naming the conversion applied.

    TODO
    """
    raise NotImplementedError


def convert_currency(amount: Field, target: str, rate_date: str | None) -> Field:
    """Convert to the target currency at a dated rate.

    Refuses when `rate_date` is None. Do not fall back to a current or average rate
    — an undated conversion is a fabricated number, and the correct product
    behaviour is to raise the gap rather than paper over it.

    TODO
    """
    raise NotImplementedError


def amortize_tooling(tooling: Field, quantity: int) -> Decimal:
    """Spread one-off tooling across the order quantity.

    This is the term that drives the break-even crossover in the UI: high-tooling
    suppliers lose at low volume and win at high volume.

    TODO
    """
    raise NotImplementedError
