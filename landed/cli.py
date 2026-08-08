"""Headless comparison.

    python -m landed.cli compare --pack packs/synthetic --quantity 10000

Exists so the pipeline can be exercised, scored, and debugged without a browser,
a database, or a login. The evaluation harness uses the same entry point, which is
what keeps reported numbers honest: they come from the path the product runs, not a
parallel one written to produce them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from landed.core.pipeline import ComparisonOutcome, SupplierOutcome, compare_pack
from landed.core.providers import get_provider
from landed.core.schema import QuoteState

STATE_MARK = {
    QuoteState.LANDED: "[LANDED]     ",
    QuoteState.CONTESTED: "[CONTESTED]  ",
    QuoteState.NOT_LANDED: "[NOT LANDED] ",
}
RULE = "-" * 78


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="landed", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    compare = sub.add_parser("compare", help="compare every supplier in a pack")
    compare.add_argument("--pack", type=Path, required=True)
    compare.add_argument("--quantity", type=int, default=10_000)
    compare.add_argument("--provider", default=None, help="anthropic | gemini | ollama")
    compare.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    if not args.pack.is_dir():
        parser.error(f"pack directory not found: {args.pack}")

    outcome = compare_pack(
        args.pack, args.quantity, provider=get_provider(args.provider)
    )
    if args.json:
        print(outcome.model_dump_json(indent=2))
    else:
        _render(outcome)
    return 0


def _render(outcome: ComparisonOutcome) -> None:
    print(f"\n{RULE}")
    print(f"LANDED COST COMPARISON   quantity {outcome.quantity:,}   {outcome.currency}")
    print(RULE)

    for supplier in _display_order(outcome):
        print(f"{STATE_MARK[supplier.state]}{_headline(supplier)}")

    print(f"\n{outcome.comparable_count} suppliers are comparable.")
    _render_blockers(outcome)
    _render_advisories(outcome)
    _render_winner(outcome)
    _render_unreadable(outcome)
    print()


def _display_order(outcome: ComparisonOutcome) -> list[SupplierOutcome]:
    """Costed suppliers first, cheapest to dearest, then everything still blocked."""
    ranked = outcome.ranked
    remaining = [s for s in outcome.suppliers if s not in ranked]
    return [*ranked, *sorted(remaining, key=lambda s: s.supplier_id)]


def _headline(supplier: SupplierOutcome) -> str:
    name = f"{supplier.supplier_id}  {supplier.supplier_name or ''}".strip()
    if supplier.breakdown is not None:
        per_unit = supplier.breakdown.per_unit.value
        total = supplier.breakdown.total.value
        return f"{name:<42}{per_unit:>10,.2f} /unit{total:>16,.2f}"
    blocking = [c for c in supplier.quotation.open_conflicts if c.blocks_total]
    reason = (
        supplier.quotation.missing[0]
        if supplier.quotation.missing
        else (blocking[0].kind.value.replace("_", " ") if blocking else "not comparable")
    )
    return f"{name:<42}{'-':>10}       {reason}"


def _render_blockers(outcome: ComparisonOutcome) -> None:
    blocked = [s for s in outcome.suppliers if s.state is not QuoteState.LANDED]
    if not blocked:
        return
    print(f"\nWHY THESE CANNOT BE COMPARED YET\n{RULE}")
    for supplier in blocked:
        print(f"  {supplier.supplier_id}  {supplier.supplier_name or ''}")
        for conflict in supplier.quotation.open_conflicts:
            if conflict.blocks_total:
                print(f"       - {conflict.message}")
        for source in _conflict_sources(supplier):
            print(f"         source: {source}")


def _conflict_sources(supplier: SupplierOutcome) -> list[str]:
    return [
        f"{source.file}"
        + (f" p.{source.page}" if source.page else "")
        + (f" {source.sheet}!{source.cell}" if source.cell else "")
        for conflict in supplier.quotation.open_conflicts
        if conflict.blocks_total
        for source in conflict.sources
    ]


def _render_advisories(outcome: ComparisonOutcome) -> None:
    advisories = [
        (s.supplier_id, c)
        for s in outcome.suppliers
        for c in s.quotation.conflicts
        if not c.blocks_total
    ]
    if not advisories:
        return
    print(f"\nWORTH A SECOND LOOK\n{RULE}")
    for supplier_id, conflict in advisories:
        print(f"  {supplier_id}  {conflict.message}")


def _render_winner(outcome: ComparisonOutcome) -> None:
    ranked = outcome.ranked
    if not ranked:
        print("\nNo supplier can be costed from the evidence supplied.")
        return
    best = ranked[0]
    print(f"\nLOWEST LANDED COST\n{RULE}")
    print(f"  {best.supplier_id}  {best.supplier_name or ''}")
    breakdown = best.breakdown
    assert breakdown is not None
    for label, term in (
        ("goods", breakdown.goods),
        ("tooling (amortized)", breakdown.tooling_amortized),
        ("freight", breakdown.freight),
        ("insurance", breakdown.insurance),
        ("duty", breakdown.duty),
        ("financing", breakdown.financing),
    ):
        print(f"    {label:<22}{term.value:>14,.2f}   {term.note or ''}")
    print(f"    {'TOTAL':<22}{breakdown.total.value:>14,.2f}")
    print(f"    {'PER UNIT':<22}{breakdown.per_unit.value:>14,.2f}")


def _render_unreadable(outcome: ComparisonOutcome) -> None:
    if not outcome.unreadable:
        return
    print(f"\nCOULD NOT BE READ\n{RULE}")
    for entry in outcome.unreadable:
        print(f"  {entry}")


if __name__ == "__main__":
    sys.exit(main())
