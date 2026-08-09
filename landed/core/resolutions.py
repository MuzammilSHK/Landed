"""Human decisions applied to a comparison.

Without this, NOT LANDED is a dead end: the product names what is missing and stops.
A buyer who phones the supplier and learns the freight rate has nowhere to put it.

A supplied value is never disguised as evidence. It lands as `Origin.ASSUMED`
carrying an `Attribution` naming who supplied it and when, so the breakdown, the UI,
and the exported report can all show it as a human's input rather than something a
document said. That distinction is the whole reason this is safe to offer.

Resolving a contradiction records which source the human sided with. The losing
value stays in the conflict — the record of what was once in dispute survives the
decision.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from .normalize import first_number, parse_incoterm, parse_price_basis
from .schema import (
    Attribution,
    Conflict,
    CostAssumptions,
    Count,
    Incoterm,
    Money,
    Origin,
    PriceBasis,
    Quotation,
    Sourced,
    Text,
)


class HumanResolution(BaseModel):
    """One value a person supplied or chose.

    `chosen_file` is set when settling a contradiction, and is what the audit trail
    shows: not merely that someone picked 10,000, but that they sided with the
    supplier profile over the quotation.
    """

    supplier_id: str
    field_path: str
    value: str
    unit: str | None = None
    currency: str | None = None
    chosen_file: str | None = None
    attribution: Attribution

    @property
    def is_source_choice(self) -> bool:
        return self.chosen_file is not None


# Fields that belong to the pack's assumptions rather than to any one quotation.
ASSUMPTION_FIELDS = {
    "freight",
    "freight_flat",
    "freight_per_kg",
    "duty_rate",
    "insurance_rate",
    "financing_annual_rate",
    "fx_rate",
    "fx_rate_date",
}

LINE_ITEM_FIELDS = {
    "unit_price",
    "price_basis",
    "moq",
    "tooling_cost",
    "lead_time_days",
    "unit_weight_kg",
}

# Assumption fields a person enters directly, and how each is carried. Rates are bare
# decimals; freight is money and needs the currency it was quoted in.
_ASSUMPTION_INPUTS: dict[str, str] = {
    "freight_flat": "money",
    "freight_per_kg": "money",
    "duty_rate": "rate",
    "insurance_rate": "rate",
    "financing_annual_rate": "rate",
    "fx_rate_to_base": "rate",
}


def assumptions_from_inputs(
    values: dict,
    base_currency: str,
    attribution: Attribution,
) -> CostAssumptions:
    """Build cost assumptions from figures a person entered.

    The counterpart to `packs.load_assumptions`, which reads the same figures out of a
    spreadsheet. Both produce the same contract; this one carries an `Attribution`
    instead of a `Source`, which is what keeps a rate somebody typed distinguishable
    from a rate a document stated everywhere it is later displayed.

    A blank or unparseable entry is left as None rather than coerced to zero. Zero
    duty is a claim; no duty rate is an absence, and the cost engine must be able to
    tell them apart in order to refuse.
    """
    supplied: dict = {}
    for field, kind in _ASSUMPTION_INPUTS.items():
        amount = _decimal(str(values.get(field) or ""))
        if amount is None:
            continue
        supplied[field] = (
            Money(
                value=amount,
                currency=base_currency,
                origin=Origin.ASSUMED,
                attribution=attribution,
                note=f"supplied by {attribution.actor}, not stated in any document",
            )
            if kind == "money"
            else Sourced[Decimal](
                value=amount,
                origin=Origin.ASSUMED,
                attribution=attribution,
                note=f"supplied by {attribution.actor}, not stated in any document",
            )
        )

    date = str(values.get("fx_rate_date") or "").strip()
    days = _decimal(str(values.get("payment_days_outstanding") or ""))

    return CostAssumptions(
        base_currency=base_currency,
        fx_rate_date=date or None,
        payment_days_outstanding=int(days) if days is not None else 0,
        **supplied,
    )


def apply(
    quotation: Quotation,
    assumptions: CostAssumptions,
    resolutions: list[HumanResolution],
) -> tuple[Quotation, CostAssumptions]:
    """Fold a supplier's resolutions into its quotation and the shared assumptions.

    Returns copies; the originals are left intact so the UI can show what changed.
    """
    mine = [r for r in resolutions if r.supplier_id == quotation.supplier_id]
    if not mine:
        return quotation, assumptions

    # Only resolutions that actually produced a value count as settling anything. A
    # value we could not parse must leave the field missing, or the quote unblocks
    # with nothing behind it — which is the exact failure this product prevents.
    applied: list[HumanResolution] = []
    for resolution in mine:
        if resolution.field_path in ASSUMPTION_FIELDS:
            assumptions, accepted = _apply_to_assumptions(assumptions, resolution)
        else:
            quotation, accepted = _apply_to_quotation(quotation, resolution)
        if accepted:
            applied.append(resolution)

    return _mark_resolved(quotation, applied), assumptions


def _apply_to_quotation(
    quotation: Quotation, resolution: HumanResolution
) -> tuple[Quotation, bool]:
    field = resolution.field_path.split(".")[-1]
    value = _coerce(field, resolution)
    if value is None:
        return quotation, False

    if field not in LINE_ITEM_FIELDS:
        return quotation.model_copy(update={field: value}), True
    if not quotation.line_items:
        return quotation, False
    items = list(quotation.line_items)
    items[0] = items[0].model_copy(update={field: value})
    return quotation.model_copy(update={"line_items": items}), True


def _apply_to_assumptions(
    assumptions: CostAssumptions, resolution: HumanResolution
) -> tuple[CostAssumptions, bool]:
    field = resolution.field_path
    if field == "fx_rate_date":
        date = resolution.value.strip()
        if not date:
            return assumptions, False
        return assumptions.model_copy(update={"fx_rate_date": date}), True

    amount = _decimal(resolution.value)
    if amount is None:
        return assumptions, False

    target = {"freight": "freight_flat", "fx_rate": "fx_rate_to_base"}.get(field, field)
    supplied = (
        _assumed_money(amount, resolution)
        if target.startswith("freight")
        else _assumed_rate(amount, resolution)
    )
    return assumptions.model_copy(update={target: supplied}), True


def _mark_resolved(
    quotation: Quotation, resolutions: list[HumanResolution]
) -> Quotation:
    """Close the conflicts these resolutions answer, keeping both original values."""
    settled = {r.field_path.split(".")[-1]: r for r in resolutions}
    conflicts = [
        conflict.model_copy(
            update={"resolved_with": settled[_leaf(conflict)].attribution}
        )
        if _leaf(conflict) in settled and conflict.is_open
        else conflict
        for conflict in quotation.conflicts
    ]
    missing = [name for name in quotation.missing if name not in settled]
    return quotation.model_copy(update={"conflicts": conflicts, "missing": missing})


def _leaf(conflict: Conflict) -> str:
    return conflict.field_path.split(".")[-1]


def _coerce(field: str, resolution: HumanResolution):
    """Turn a supplied string into the type the field expects.

    Returns None when it cannot, so a nonsensical entry leaves the quote blocked
    rather than replacing a known gap with a wrong value.
    """
    attribution = resolution.attribution
    raw = resolution.value.strip()

    if field == "incoterm":
        term = parse_incoterm(raw)
        return (
            None
            if term is None
            else Sourced[Incoterm](
                value=term, origin=Origin.ASSUMED, attribution=attribution
            )
        )
    if field == "price_basis":
        basis = parse_price_basis(raw)
        return (
            None
            if basis is None
            else Sourced[PriceBasis](
                value=basis, origin=Origin.ASSUMED, attribution=attribution
            )
        )
    if field in {"moq", "lead_time_days", "validity_days"}:
        amount = _decimal(raw)
        return (
            None
            if amount is None
            else Count(value=int(amount), origin=Origin.ASSUMED, attribution=attribution)
        )
    if field in {"unit_price", "tooling_cost", "unit_weight_kg"}:
        amount = _decimal(raw)
        return (
            None
            if amount is None
            else Money(
                value=amount,
                currency=resolution.currency,
                unit=resolution.unit,
                origin=Origin.ASSUMED,
                attribution=attribution,
            )
        )
    return Text(value=raw, origin=Origin.ASSUMED, attribution=attribution)


def _assumed_money(amount: Decimal, resolution: HumanResolution) -> Money:
    return Money(
        value=amount,
        currency=resolution.currency,
        origin=Origin.ASSUMED,
        attribution=resolution.attribution,
        note=_note(resolution),
    )


def _assumed_rate(amount: Decimal, resolution: HumanResolution) -> Sourced[Decimal]:
    return Sourced[Decimal](
        value=amount,
        origin=Origin.ASSUMED,
        attribution=resolution.attribution,
        note=_note(resolution),
    )


def _note(resolution: HumanResolution) -> str:
    who = resolution.attribution.actor
    if resolution.chosen_file:
        return f"{who} chose the value stated in {resolution.chosen_file}"
    return f"supplied by {who}, not stated in any document"


def _decimal(text: str) -> Decimal | None:
    return first_number(text)
