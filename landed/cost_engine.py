"""Deterministic landed-cost computation.

**No LLM call may ever appear in this module.** That is the rule the whole project
rests on: a model reconciles messy documents, arithmetic stays in code that can be
unit-tested and diffed against the organizer's reference calculations. If this file
ever imports an SDK, the guarantee is gone.

The guard clause runs first and runs always. Missing a required field returns a
`Refusal`, not a partial total and not a zero. A tidy number produced from an
assumed freight term is precisely the failure the brief warns about.

    landed = goods
           + tooling_amortized
           + freight
           + duty
           + insurance
           + financing

Every term is returned itemized. The total is a convenience; the breakdown is the
product.
"""

from __future__ import annotations

from decimal import Decimal

from .schema import (
    REQUIRED_FOR_TOTAL,
    CostBreakdown,
    CostResult,
    Field,
    Quotation,
    Refusal,
)


def compute(quote: Quotation, quantity: int, assumptions: dict) -> CostResult:
    """Compute landed cost for one quote at one order quantity.

    Returns CostBreakdown when the quote is complete, Refusal when it is not.
    Never raises on incomplete input — incompleteness is an expected state with a
    designed response, not an error.

    TODO: guard first, then accumulate terms.
    """
    raise NotImplementedError


def _guard(quote: Quotation) -> Refusal | None:
    """Return a Refusal if any REQUIRED_FOR_TOTAL field is absent.

    TODO
    """
    raise NotImplementedError


def _goods(quote: Quotation, quantity: int) -> Field:
    """Normalized unit price x quantity. TODO"""
    raise NotImplementedError


def _freight(quote: Quotation, quantity: int, assumptions: dict) -> Field:
    """Freight per the pack's stated logistics assumptions.

    Incoterm determines who bears which leg — EXW puts everything on the buyer,
    DDP almost nothing. Getting this wrong silently shifts thousands of dollars.

    TODO
    """
    raise NotImplementedError


def _duty(quote: Quotation, goods_value: Decimal, assumptions: dict) -> Field:
    """Duty from the pack's stated rates.

    Use only rates supplied in the challenge pack. Do not infer HS codes or look up
    external tariff schedules — the brief is explicit that inferred compliance data
    must never be presented as verified fact.

    TODO
    """
    raise NotImplementedError


def _insurance(goods_value: Decimal, assumptions: dict) -> Field:
    """TODO"""
    raise NotImplementedError


def _financing(quote: Quotation, goods_value: Decimal, assumptions: dict) -> Field:
    """Working-capital cost implied by the payment terms.

    30% advance / 70% on shipment ties up cash differently from net-60, and that
    difference is real money the headline unit price never shows.

    TODO
    """
    raise NotImplementedError


def break_even(quotes: list[Quotation], assumptions: dict,
               qty_range: range) -> dict[str, list[Decimal]]:
    """Landed cost per unit across a quantity sweep, per supplier.

    Feeds the break-even chart. The crossover point — where tooling amortization
    flips the ranking — is usually the single most persuasive thing on screen.

    TODO
    """
    raise NotImplementedError
