"""Extraction tests, run against a stub provider.

No network and no key. What is worth testing here is not whether a model reads a PDF
well — it is what we do with what it returns: citations built locally, uncited values
dropped, and a scan never mislabelled as a text read.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from landed.core.extract import (
    QUOTATION_SCHEMA,
    build_request,
    extract_profile,
    extract_quotation,
    scan_for_injection,
)
from landed.core.ingest import ingest_file
from landed.core.providers import ExtractionRequest, ExtractionResponse
from landed.core.schema import Incoterm, PriceBasis, ReadMethod

PACK = Path(__file__).resolve().parents[1] / "packs" / "synthetic"


class StubProvider:
    """Returns a fixed payload and records what it was asked."""

    name = "stub"
    model = "stub-1"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.seen: ExtractionRequest | None = None

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.seen = request
        return ExtractionResponse(
            payload=self.payload, provider=self.name, model_version=self.model
        )


def field(value, page: int = 1, **extra) -> dict:
    return {"value": value, "page": page, "excerpt": str(value), **extra}


def quotation_payload(**overrides) -> dict:
    payload = {
        "supplier_name": field("Shenzhen Precision Metalworks"),
        "currency": field("USD"),
        "incoterm": field("FOB Shenzhen"),
        "quote_date": field("2026-07-14"),
        "validity_days": field(30),
        "payment_terms": field("30% advance, 70% against documents"),
        "line_items": [
            {
                "unit_price": field("USD 12.40", page=1),
                "price_basis": field("per piece"),
                "moq": field("5,000 pieces"),
                "tooling_cost": field("8,400.00"),
                "lead_time_days": field(35),
                "unit_weight_kg": field("0.42"),
            }
        ],
    }
    payload.update(overrides)
    return payload


def read(name: str):
    return ingest_file(PACK / name)


class TestValueMapping:
    def test_prices_parse_through_currency_marks_and_separators(self) -> None:
        quote, _, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(quotation_payload())
        )
        item = quote.line_items[0]
        assert item.unit_price.value == Decimal("12.40")
        assert item.tooling_cost.value == Decimal("8400.00")
        assert item.moq.value == 5000

    def test_incoterm_is_parsed_out_of_free_text(self) -> None:
        quote, _, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(quotation_payload())
        )
        assert quote.incoterm.value is Incoterm.FOB

    def test_price_basis_is_parsed(self) -> None:
        quote, _, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(quotation_payload())
        )
        assert quote.line_items[0].price_basis.value is PriceBasis.PER_PIECE

    def test_unparseable_incoterm_is_dropped_rather_than_guessed(self) -> None:
        payload = quotation_payload(incoterm=field("to be discussed"))
        quote, _, _ = extract_quotation(read("quote_a.pdf"), "A", StubProvider(payload))
        assert quote.incoterm is None


class TestCitations:
    def test_source_points_at_the_ingested_file(self) -> None:
        """The filename comes from what we ingested, not from what the model says."""
        quote, _, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(quotation_payload())
        )
        assert quote.currency.source.file == "quote_a.pdf"
        assert quote.currency.source.page == 1

    def test_uncited_value_is_dropped(self) -> None:
        """An uncited number is indistinguishable from a fabricated one."""
        payload = quotation_payload(currency={"value": "USD", "excerpt": "USD"})
        quote, _, _ = extract_quotation(read("quote_a.pdf"), "A", StubProvider(payload))
        assert quote.currency is None

    def test_empty_value_is_dropped(self) -> None:
        payload = quotation_payload(payment_terms=field(""))
        quote, _, _ = extract_quotation(read("quote_a.pdf"), "A", StubProvider(payload))
        assert quote.payment_terms is None

    def test_excerpt_is_retained_for_verification(self) -> None:
        payload = quotation_payload(
            currency={"value": "USD", "page": 1, "excerpt": "Unit price: USD 12.40"}
        )
        quote, _, _ = extract_quotation(read("quote_a.pdf"), "A", StubProvider(payload))
        assert quote.currency.source.excerpt == "Unit price: USD 12.40"


class TestReadMethod:
    def test_text_layer_document_is_marked_as_such(self) -> None:
        quote, _, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(quotation_payload())
        )
        assert quote.currency.source.read_method is ReadMethod.TEXT_LAYER
        assert quote.currency.needs_verification is False

    def test_scanned_document_is_marked_for_verification(self) -> None:
        """Decided from what we ingested — a model cannot pass a scan off as a read."""
        quote, _, _ = extract_quotation(
            read("quote_e.png"), "E", StubProvider(quotation_payload())
        )
        assert quote.currency.source.read_method is ReadMethod.VISION
        assert quote.currency.needs_verification is True


class TestInjectionScanning:
    def test_hostile_profile_is_flagged(self) -> None:
        found = scan_for_injection(read("profile_d.pdf"))
        assert len(found) == 1
        assert "not acted on" in found[0].message

    def test_flag_does_not_block_the_total(self) -> None:
        """It is a warning to a human, not grounds to withhold a comparison."""
        assert scan_for_injection(read("profile_d.pdf"))[0].blocks_total is False

    def test_clean_document_is_not_flagged(self) -> None:
        assert scan_for_injection(read("quote_a.pdf")) == []

    def test_detection_does_not_depend_on_the_model(self) -> None:
        """The deterministic scan fires even when the model reports nothing."""
        _, conflicts, _ = extract_profile(
            read("profile_d.pdf"), "D", StubProvider({"supplier_name": field("Hanoi")})
        )
        assert conflicts

    def test_model_report_is_honoured_when_the_scan_is_silent(self) -> None:
        payload = quotation_payload(
            injection_suspected={"found": True, "excerpt": "see attached instructions"}
        )
        _, conflicts, _ = extract_quotation(
            read("quote_a.pdf"), "A", StubProvider(payload)
        )
        assert len(conflicts) == 1


class TestRequestConstruction:
    def test_document_text_reaches_the_provider(self) -> None:
        document = read("quote_a.pdf")
        request = build_request(document, "Extract.", QUOTATION_SCHEMA)
        assert any("12.40" in block for block in request.document_text)

    def test_scanned_document_is_sent_as_an_image(self) -> None:
        request = build_request(read("quote_e.png"), "Extract.", QUOTATION_SCHEMA)
        assert request.images
        assert request.document_text == []

    def test_provider_receives_the_schema(self) -> None:
        provider = StubProvider(quotation_payload())
        extract_quotation(read("quote_a.pdf"), "A", provider)
        assert provider.seen.json_schema == QUOTATION_SCHEMA


class TestProfiles:
    def test_profile_fields_are_mapped(self) -> None:
        payload = {
            "supplier_name": field("Guangzhou Hardline Industrial"),
            "moq": field("10,000 pieces"),
            "lead_time_days": field(42),
            "certifications": [field("ISO 9001:2015"), field("RoHS")],
        }
        profile, _, _ = extract_profile(
            read("profile_b.docx"), "B", StubProvider(payload)
        )
        assert profile.moq.value == 10_000
        assert profile.lead_time_days.value == 42
        assert [c.value for c in profile.certifications] == ["ISO 9001:2015", "RoHS"]
