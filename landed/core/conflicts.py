"""What cannot be compared yet, and why.

This is the differentiator. A landed-cost calculator is a spreadsheet with better
parsing; the thing worth building is the part that says *these two quotes are not
comparable, here is the specific reason, here are both sources*.

Detectors are independent and boring. Each answers one question, none mutates the
quote, and none resolves anything — resolution is the human's, which is also where
the brief draws the approval line. `annotate` runs them all and returns a copy of the
quote with `missing` and `conflicts` populated.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from .cost_engine import missing_inputs, normalized_unit_price
from .schema import (
    Conflict,
    ConflictKind,
    CostAssumptions,
    Origin,
    Quotation,
    Sourced,
    SupplierProfile,
)

# A stated basis parsed with less certainty than this is worth confirming: mistaking
# per-1000 for per-piece is a 1000x error that looks entirely plausible on screen.
BASIS_CONFIDENCE_FLOOR = 0.8

# Ratio between the cheapest and dearest normalized unit price above which a basis
# misread is more likely than a genuine price difference.
PRICE_OUTLIER_RATIO = Decimal(100)

_FIELD_LABELS = {
    "unit_price": "unit price",
    "price_basis": "pricing basis (per piece, per 1000, per kg)",
    "currency": "currency",
    "incoterm": "delivery terms (Incoterm)",
    "freight": "freight rate or terms",
    "duty_rate": "import duty rate",
    "fx_rate": "dated exchange rate",
    "unit_weight_kg": "unit weight, needed for weight-based freight",
    "unit_price_basis_conversion": "enough detail to restate the price per piece",
}


def annotate(
    quote: Quotation,
    quantity: int,
    assumptions: CostAssumptions,
    profile: SupplierProfile | None = None,
    peers: Sequence[Quotation] = (),
    extra: Sequence[Conflict] = (),
) -> Quotation:
    """Return a copy of `quote` with its gaps and conflicts recorded.

    `extra` carries conflicts found earlier in the pipeline — injection attempts
    spotted during extraction, for instance — so everything the user must know about
    a supplier arrives in one place.
    """
    found: list[Conflict] = [
        *detect_missing(quote, quantity, assumptions),
        *detect_undated_currency(quote, assumptions),
        *detect_ambiguous_basis(quote),
        *detect_vision_reads(quote),
        *extra,
    ]
    if profile is not None:
        found.extend(detect_contradictions(quote, profile))
    if peers:
        found.extend(detect_price_outlier(quote, peers, assumptions))
    return quote.model_copy(
        update={
            "missing": missing_inputs(quote, quantity, assumptions),
            "conflicts": found,
        }
    )


def detect_missing(
    quote: Quotation, quantity: int, assumptions: CostAssumptions
) -> list[Conflict]:
    """Required inputs that were never found.

    Shares `cost_engine.missing_inputs` so the refusal and the explanation cannot
    disagree. Messages name the field in the user's language, not the schema's.
    """
    return [
        Conflict(
            kind=ConflictKind.MISSING_REQUIRED,
            field_path=name,
            message=f"{_FIELD_LABELS.get(name, name)} not stated",
        )
        for name in missing_inputs(quote, quantity, assumptions)
    ]


def detect_contradictions(
    quote: Quotation, profile: SupplierProfile
) -> list[Conflict]:
    """The same fact asserted differently by two documents.

    Both values are kept with their sources and neither is chosen — picking one is
    the unverified-claim behaviour the brief prohibits.
    """
    conflicts: list[Conflict] = []
    first = quote.line_items[0] if quote.line_items else None
    pairs = (
        ("moq", "minimum order quantity", first.moq if first else None, profile.moq),
        (
            "lead_time_days",
            "production lead time",
            first.lead_time_days if first else None,
            profile.lead_time_days,
        ),
    )
    for path, label, quoted, stated in pairs:
        if quoted is None or stated is None or quoted.value == stated.value:
            continue
        conflicts.append(
            Conflict(
                kind=ConflictKind.CONTRADICTION,
                field_path=path,
                message=(
                    f"{label} disagrees: quotation states {quoted.value}, "
                    f"supplier profile states {stated.value}"
                ),
                sources=[s for s in (quoted.source, stated.source) if s],
                values=[str(quoted.value), str(stated.value)],
            )
        )
    return conflicts


def detect_undated_currency(
    quote: Quotation, assumptions: CostAssumptions
) -> list[Conflict]:
    """Foreign currency with no date to convert at.

    Any rate chosen here would be invented. A declared assumption is defensible; a
    silent one is not.
    """
    if quote.currency is None or quote.currency.value == assumptions.base_currency:
        return []
    if assumptions.fx_rate_to_base is not None and assumptions.fx_rate_date:
        return []
    return [
        Conflict(
            kind=ConflictKind.UNDATED_CURRENCY,
            field_path="currency",
            message=(
                f"quoted in {quote.currency.value} with no dated rate to "
                f"{assumptions.base_currency} — supply the rate and its date"
            ),
            sources=[s for s in (quote.currency.source,) if s],
        )
    ]


def detect_ambiguous_basis(quote: Quotation) -> list[Conflict]:
    """A pricing basis inferred rather than stated.

    Normalization handles a *declared* per-1000 price correctly. The danger is a
    basis nobody declared, where a wrong guess moves the total by 1000x.
    """
    conflicts: list[Conflict] = []
    for index, item in enumerate(quote.line_items):
        basis = item.price_basis
        if basis is None or not _was_inferred(basis):
            continue
        conflicts.append(
            Conflict(
                kind=ConflictKind.UNIT_MISMATCH,
                field_path=f"line_items[{index}].price_basis",
                message=(
                    f"pricing basis was not clearly stated; read as "
                    f"{basis.value.value.replace('_', ' ')} — confirm, since a "
                    f"per-1000 price misread as per-piece is a 1000x error"
                ),
                sources=[s for s in (basis.source,) if s],
                values=[basis.value.value],
            )
        )
    return conflicts


def detect_price_outlier(
    quote: Quotation, peers: Sequence[Quotation], assumptions: CostAssumptions
) -> list[Conflict]:
    """A normalized price wildly out of step with the other suppliers.

    Catches a basis misread that slipped through with high confidence: if one
    supplier's per-unit price is a hundred times the rest, a unit error is likelier
    than a genuine quote.
    """
    mine = _first_price(quote, assumptions)
    others = [p for p in (_first_price(peer, assumptions) for peer in peers) if p]
    if mine is None or not others or mine == 0:
        return []
    median = sorted(others)[len(others) // 2]
    if median == 0:
        return []
    ratio = mine / median if mine > median else median / mine
    if ratio < PRICE_OUTLIER_RATIO:
        return []
    return [
        Conflict(
            kind=ConflictKind.UNIT_MISMATCH,
            field_path="line_items[0].unit_price",
            message=(
                f"normalized unit price is {ratio:.0f}x the median of the other "
                f"suppliers — check the pricing basis before comparing"
            ),
            values=[str(mine), str(median)],
        )
    ]


def detect_vision_reads(quote: Quotation) -> list[Conflict]:
    """Values read from an image rather than a text layer.

    Advisory, never blocking: worth a second look, not worth withholding a total.
    """
    flagged = [
        (f"line_items[{i}].{name}", field)
        for i, item in enumerate(quote.line_items)
        for name, field in (("unit_price", item.unit_price), ("moq", item.moq))
        if field is not None and field.needs_verification
    ]
    return [
        Conflict(
            kind=ConflictKind.VISION_READ,
            field_path=path,
            message=(
                f"read from a scanned image on page {field.source.page} — "
                f"verify against the source"
            ),
            sources=[field.source] if field.source else [],
            values=[str(field.value)],
            blocks_total=False,
        )
        for path, field in flagged
    ]


def detect_stale(quote: Quotation, as_of: date) -> list[Conflict]:
    """Quote past its stated validity window. Advisory, not blocking."""
    if quote.quote_date is None or quote.validity_days is None:
        return []
    quoted_on = _parse_date(quote.quote_date.value)
    if quoted_on is None:
        return []
    elapsed = (as_of - quoted_on).days
    if elapsed <= quote.validity_days.value:
        return []
    return [
        Conflict(
            kind=ConflictKind.STALE_QUOTE,
            field_path="quote_date",
            message=(
                f"quote expired {elapsed - quote.validity_days.value} days ago "
                f"(valid {quote.validity_days.value} days from {quoted_on})"
            ),
            sources=[s for s in (quote.quote_date.source,) if s],
            blocks_total=False,
        )
    ]


def _was_inferred(field: Sourced) -> bool:
    if field.origin is Origin.MODEL or field.source is None:
        return True
    return field.confidence is not None and field.confidence < BASIS_CONFIDENCE_FLOOR


def _first_price(quote: Quotation, assumptions: CostAssumptions) -> Decimal | None:
    """Normalized per-unit price of the first line item, for cross-supplier sanity."""
    return normalized_unit_price(quote, 1, assumptions)


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()[:10])
    except (ValueError, AttributeError):
        return None
