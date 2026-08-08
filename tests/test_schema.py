"""Schema contract tests.

The provenance guarantee has to hold structurally. If a bare float can reach the
cost engine, "every value carries its source" becomes a claim we make in the pitch
rather than a property of the system.
"""

from __future__ import annotations

import pytest


class TestProvenance:
    @pytest.mark.skip(reason="TODO")
    def test_field_requires_source_when_extracted(self) -> None: ...

    @pytest.mark.skip(reason="TODO")
    def test_bare_value_rejected_by_cost_engine(self) -> None: ...


class TestOrigin:
    @pytest.mark.skip(reason="TODO")
    def test_extracted_assumed_derived_stay_distinct(self) -> None:
        """The brief requires facts, assumptions, and model output be separable
        at evaluation time."""


class TestState:
    @pytest.mark.skip(reason="TODO")
    def test_missing_field_yields_not_landed(self) -> None: ...

    @pytest.mark.skip(reason="TODO")
    def test_blocking_conflict_yields_contested(self) -> None: ...

    @pytest.mark.skip(reason="TODO")
    def test_clean_quote_yields_landed(self) -> None: ...
