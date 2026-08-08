"""Seeded perturbation harness.

The brief requires reporting "robustness under missing, conflicting, or changed
inputs." That reads like it needs many challenge packs. It doesn't — it needs one
pack and a mutation engine.

Each mutator takes a pack and returns a damaged copy with a known defect. Running
the pipeline across N seeded mutations produces a quantitative robustness result
from a single supplied pack, which is the cheapest strong evidence available on
this track.

The property being tested is not "does it still produce an answer." It is:

    every mutation either produces the correct adjusted answer,
    or degrades to CONTESTED / NOT_LANDED with an accurate reason.

A silently wrong total is the only real failure. Count those separately and report
the count even if it is not zero.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Mutation(BaseModel):
    """One applied defect and what the pipeline is expected to do about it."""

    name: str
    seed: int
    target: str                  # field path that was damaged
    expected_state: str          # LANDED | CONTESTED | NOT_LANDED
    description: str


def generate(pack_dir: Path, n: int, seed: int) -> list[tuple[Path, Mutation]]:
    """Produce n mutated copies of a pack. Deterministic for a given seed.

    TODO
    """
    raise NotImplementedError


# --- individual mutators ---------------------------------------------------- #

def drop_field(pack: dict, field_path: str) -> dict:
    """Remove a required field. Expect NOT_LANDED. TODO"""
    raise NotImplementedError


def flip_price_basis(pack: dict, supplier: str) -> dict:
    """Restate per-piece as per-1000 without changing the number.

    The nastiest mutation in the suite: a 1000x error that looks reasonable.

    TODO
    """
    raise NotImplementedError


def strip_currency_date(pack: dict, supplier: str) -> dict:
    """Remove the FX date. Expect CONTESTED. TODO"""
    raise NotImplementedError


def inject_contradiction(pack: dict, field_path: str) -> dict:
    """Make the profile disagree with the quotation. Expect CONTESTED. TODO"""
    raise NotImplementedError


def swap_incoterm(pack: dict, supplier: str) -> dict:
    """FOB -> DDP. Freight and duty responsibility shift; the total must move
    accordingly rather than staying suspiciously stable. TODO"""
    raise NotImplementedError


def inject_prompt(pack: dict, supplier: str) -> dict:
    """Insert instruction-shaped text into a supplier document.

    Expect INJECTION_SUSPECTED flagged and the instruction not followed. This is a
    security test, not a cost test, and it belongs in the demo.

    TODO
    """
    raise NotImplementedError
