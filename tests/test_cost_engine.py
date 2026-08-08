"""Cost engine tests.

This is the module that must be provably correct — it is the one producing the
number judges check against the organizer's reference calculations. Tests here are
worth more than tests anywhere else in the project.

Priority order if time runs short:
  1. the refusal guard        (safety behaviour, demonstrated in the pitch)
  2. price-basis conversion   (the 1000x error)
  3. tooling amortization     (drives the break-even story)
  4. everything else
"""

from __future__ import annotations

import pytest


class TestGuard:
    """Refusal behaviour — the fallback case the brief requires demonstrated."""

    @pytest.mark.skip(reason="TODO")
    def test_missing_incoterm_returns_refusal(self) -> None:
        """No freight terms -> Refusal, never a partial or zeroed total."""

    @pytest.mark.skip(reason="TODO")
    def test_refusal_names_every_missing_field(self) -> None:
        """The user must learn everything that's missing in one pass, not
        discover the next gap only after fixing the first."""

    @pytest.mark.skip(reason="TODO")
    def test_guard_runs_before_any_arithmetic(self) -> None:
        """An incomplete quote must never reach a computation path."""


class TestPriceBasis:
    @pytest.mark.skip(reason="TODO")
    def test_per_1000_converts_to_per_piece(self) -> None:
        """$1,240 per 1000 -> $1.24 per piece."""

    @pytest.mark.skip(reason="TODO")
    def test_conversion_is_recorded_as_derived(self) -> None:
        """A converted value must not masquerade as one read from the document."""


class TestTooling:
    @pytest.mark.skip(reason="TODO")
    def test_amortization_falls_with_quantity(self) -> None: ...

    @pytest.mark.skip(reason="TODO")
    def test_break_even_crossover_is_found(self) -> None:
        """High-tooling supplier loses at low volume, wins at high volume."""


class TestDeterminism:
    @pytest.mark.skip(reason="TODO")
    def test_identical_input_gives_identical_output(self) -> None:
        """Reproducibility is a graded deliverable, not a nicety."""

    @pytest.mark.skip(reason="TODO")
    def test_no_model_client_imported(self) -> None:
        """Assert cost_engine's module graph contains no LLM SDK.

        The project's central guarantee is that arithmetic never passes through a
        model. Enforce it with a test rather than trusting a code review at 3am.
        """
