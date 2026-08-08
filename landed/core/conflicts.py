"""What you cannot compare yet, and why.

This is the differentiator. A landed-cost calculator is a spreadsheet with better
parsing; the thing worth building is the part that says *"these two quotes are not
comparable, here is the specific reason, here are both sources."*

Detectors are deliberately independent and boring. Each answers one question and
returns Conflicts. None of them mutates the quote, and none of them resolves
anything — resolution is the human's job, which is also where the brief draws the
human-approval line.
"""

from __future__ import annotations

from .schema import Conflict, Quotation


def detect_all(quote: Quotation, peers: list[Quotation]) -> list[Conflict]:
    """Run every detector. `peers` enables cross-source contradiction checks.

    TODO
    """
    raise NotImplementedError


def detect_missing(quote: Quotation) -> list[Conflict]:
    """Required fields that were never found.

    Produces the NOT_LANDED state. Message names the field in the user's language,
    not the schema's: "freight terms not stated", not "incoterm is None".

    TODO
    """
    raise NotImplementedError


def detect_contradiction(quote: Quotation, peers: list[Quotation]) -> list[Conflict]:
    """The same fact asserted differently by two sources.

    Canonical case: MOQ is 5,000 in the quotation and 10,000 in the supplier
    profile. Show both with their sources. Do not pick — picking is exactly the
    unverified-claim behaviour the brief prohibits.

    TODO
    """
    raise NotImplementedError


def detect_unit_mismatch(quotes: list[Quotation]) -> list[Conflict]:
    """Price bases that cannot be compared as stated.

    Per-piece against per-1000 is the expensive one: a 1000x error that looks
    entirely plausible on screen.

    TODO
    """
    raise NotImplementedError


def detect_undated_currency(quote: Quotation) -> list[Conflict]:
    """Foreign currency with no date to convert at.

    Any rate chosen here would be invented. Flag it and let the user supply the
    date — a declared assumption is defensible, a silent one is not.

    TODO
    """
    raise NotImplementedError


def detect_stale(quote: Quotation, as_of: str) -> list[Conflict]:
    """Quote past its stated validity window. Non-blocking; advisory. TODO"""
    raise NotImplementedError
