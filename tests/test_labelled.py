"""Deterministic reading tests.

The point of this pass is that a labelled document costs nothing to read. So the
assertion that matters most is not that it parses well — it is that a document it
handles fully never reaches a model, and that one it cannot handle still does.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from landed.core import labelled
from landed.core.extract import REQUIRED_FIELDS, extract_profile, extract_quotation
from landed.core.ingest import ingest_file
from landed.core.schema import Incoterm, PriceBasis, ReadMethod

PACK = Path(__file__).resolve().parents[1] / "packs" / "synthetic"


class RefusingProvider:
    """Fails loudly if called. Any test using it asserts no model was needed."""

    name, model = "refusing", "none"

    def extract(self, request):
        raise AssertionError("a model was called for a document read deterministically")


def read(name: str):
    return ingest_file(PACK / name)


class TestLabelledReading:
    def test_a_text_quotation_yields_everything_a_total_needs(self) -> None:
        payload = labelled.read_quotation(read("quote_a.pdf"))
        assert labelled.missing_from(payload, REQUIRED_FIELDS) == []

    def test_values_are_read_from_the_printed_line(self) -> None:
        payload = labelled.read_quotation(read("quote_a.pdf"))
        item = payload["line_items"][0]
        assert "12.40" in item["unit_price"]["value"]
        assert item["moq"]["value"].startswith("5,000")
        assert payload["currency"]["value"] == "USD"

    def test_every_field_carries_the_line_it_came_from(self) -> None:
        """A citation derived from the literal text, not from a page a model named."""
        payload = labelled.read_quotation(read("quote_a.pdf"))
        field = payload["line_items"][0]["unit_price"]
        assert field["page"] == 1
        assert "Unit price" in field["excerpt"]

    def test_a_spreadsheet_quotation_is_read_with_cell_references(self) -> None:
        payload = labelled.read_quotation(read("quote_d.xlsx"))
        price = payload["line_items"][0]["unit_price"]
        assert "11900" in price["value"]
        assert price["cell"]
        assert price["sheet"] == "Quotation"

    def test_currency_and_basis_come_off_the_price_line(self) -> None:
        payload = labelled.read_quotation(read("quote_d.xlsx"))
        assert payload["currency"]["value"] == "EUR"
        assert "per 1000" in payload["line_items"][0]["price_basis"]["value"]

    def test_the_supplier_name_comes_from_the_heading(self) -> None:
        """It is almost never a labelled field — it is the title of the document."""
        payload = labelled.read_quotation(read("quote_a.pdf"))
        assert payload["supplier_name"]["value"] == "Shenzhen Precision Metalworks"

    def test_a_heading_needs_a_document_type_word(self) -> None:
        """Otherwise a letterhead or a page number becomes a supplier name."""
        payload = labelled.read_quotation(read("bom.csv"))
        assert "supplier_name" not in payload

    def test_a_word_profile_is_read(self) -> None:
        payload = labelled.read_profile(read("profile_b.docx"))
        assert payload["moq"]["value"].startswith("10,000")

    def test_a_document_with_no_delivery_terms_reports_it_missing(self) -> None:
        payload = labelled.read_quotation(read("quote_c.pdf"))
        assert "incoterm" in labelled.missing_from(payload, REQUIRED_FIELDS)

    def test_a_scan_yields_nothing(self) -> None:
        """No text layer, so nothing to match. The model has to read it."""
        assert labelled.read_quotation(read("quote_e.png")) == {}


class TestNoModelWhenNotNeeded:
    @pytest.mark.parametrize("name", ["quote_a.pdf", "quote_b.pdf", "quote_d.xlsx"])
    def test_a_fully_labelled_quotation_needs_no_model(self, name: str) -> None:
        quote, _, _ = extract_quotation(read(name), "X", RefusingProvider())
        assert quote.line_items[0].unit_price is not None
        assert quote.incoterm is not None

    def test_a_fully_labelled_profile_needs_no_model(self) -> None:
        profile, _, _ = extract_profile(read("profile_b.docx"), "B", RefusingProvider())
        assert profile.moq.value == 10_000

    def test_the_parsed_values_are_the_right_ones(self) -> None:
        quote, _, _ = extract_quotation(read("quote_a.pdf"), "A", RefusingProvider())
        item = quote.line_items[0]
        assert item.unit_price.value == Decimal("12.40")
        assert item.price_basis.value is PriceBasis.PER_PIECE
        assert item.moq.value == 5_000
        assert item.tooling_cost.value == Decimal("8400.00")
        assert quote.incoterm.value is Incoterm.FOB
        assert item.unit_price.source.read_method is ReadMethod.TEXT_LAYER


class TestFallingBackToTheModel:
    def test_a_scan_still_reaches_the_model(self) -> None:
        with pytest.raises(AssertionError, match="a model was called"):
            extract_quotation(read("quote_e.png"), "E", RefusingProvider())

    def test_an_incomplete_document_still_reaches_the_model(self) -> None:
        """Supplier C states no delivery terms, so the labelled pass is not enough."""
        with pytest.raises(AssertionError, match="a model was called"):
            extract_quotation(read("quote_c.pdf"), "C", RefusingProvider())


class TestMerging:
    def test_the_deterministic_pass_wins_where_it_found_something(self) -> None:
        """It read the literal line; the model nominated a page."""
        primary = {"currency": {"value": "USD", "page": 1, "excerpt": "printed"}}
        secondary = {"currency": {"value": "EUR", "page": 9, "excerpt": "guessed"}}
        assert labelled.merge(primary, secondary)["currency"]["value"] == "USD"

    def test_the_model_fills_what_the_patterns_missed(self) -> None:
        primary = {"currency": {"value": "USD", "page": 1, "excerpt": "printed"}}
        secondary = {"incoterm": {"value": "FOB", "page": 1, "excerpt": "read"}}
        merged = labelled.merge(primary, secondary)
        assert merged["currency"]["value"] == "USD"
        assert merged["incoterm"]["value"] == "FOB"

    def test_line_items_merge_field_by_field(self) -> None:
        primary = {"line_items": [{"unit_price": {"value": "12.40", "page": 1,
                                                  "excerpt": "printed"}}]}
        secondary = {"line_items": [{"unit_price": {"value": "99", "page": 2,
                                                    "excerpt": "guessed"},
                                     "moq": {"value": "5000", "page": 2,
                                             "excerpt": "read"}}]}
        item = labelled.merge(primary, secondary)["line_items"][0]
        assert item["unit_price"]["value"] == "12.40"
        assert item["moq"]["value"] == "5000"
