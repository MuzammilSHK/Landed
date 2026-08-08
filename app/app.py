"""Landed — Streamlit interface.

Layout is driven by the three states, not by the cost table. A judge should see
"two of these three quotes haven't landed" before they see any number.

    ┌ Supplier comparison ──────────────────────────────┐
    │  ✅ LANDED      Supplier A    $14.82 / unit       │
    │  ⚠️ CONTESTED   Supplier B    MOQ conflict        │
    │  ⛔ NOT LANDED  Supplier C    freight terms missing│
    └───────────────────────────────────────────────────┘
    ┌ Why these can't be compared ──────────────────────┐  <- the headline panel
    ┌ Cost breakdown (LANDED only) ─────────────────────┐
    ┌ Break-even ───────────────────────────────────────┐
    ┌ Provenance drawer ────────────────────────────────┐

Styling stays default. Every hour spent on CSS is an hour not spent on evidence,
and the rubric weights evidence at 25% against 20% for the product surface.
"""

from __future__ import annotations

import streamlit as st


def main() -> None:
    st.set_page_config(page_title="Landed", page_icon="📦", layout="wide")
    st.title("Landed")
    st.caption("No cost lands without its evidence.")

    st.info(
        "Decision support only. Landed does not contact suppliers, request "
        "quotations, approve vendors, or place orders. Every consequential "
        "action requires explicit human confirmation."
    )

    # TODO: pack selector -> ingest -> extract -> normalize -> conflicts -> cost
    # TODO: render_state_summary()
    # TODO: render_conflicts()        <- headline panel
    # TODO: render_breakdown()
    # TODO: render_break_even()
    # TODO: render_provenance_drawer()
    st.warning("Scaffold only — pipeline not yet wired.")


def render_state_summary() -> None:
    """Three-state supplier table. First thing on screen. TODO"""
    raise NotImplementedError


def render_conflicts() -> None:
    """The panel the product is actually about.

    Each conflict: plain-language reason, both values where they disagree, and a
    link to every source. No conflict is ever auto-resolved.

    TODO
    """
    raise NotImplementedError


def render_breakdown() -> None:
    """Itemized cost, LANDED suppliers only.

    Extracted facts, our assumptions, and derived values must be visually distinct
    — the brief requires they stay separable.

    TODO
    """
    raise NotImplementedError


def render_break_even() -> None:
    """Landed cost per unit vs order quantity, crossover marked. TODO"""
    raise NotImplementedError


def render_provenance_drawer() -> None:
    """Click any value -> file, page, verbatim excerpt. TODO"""
    raise NotImplementedError


if __name__ == "__main__":
    main()
