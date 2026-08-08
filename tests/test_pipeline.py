"""End-to-end pipeline tests against the synthetic pack.

Driven by a stub provider that returns the values actually printed in each document,
so this exercises grouping, assumption loading, conflict detection, costing, and the
three-state model together — without a network call or an API key.

The expectations come from `packs/synthetic/manifest.json`, so a change to the pack
that stops it carrying a seeded defect fails here rather than passing quietly.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from landed.core.packs import group_by_supplier, load_assumptions, supplier_id_from_filename
from landed.core.pipeline import compare_pack
from landed.core.providers import ExtractionRequest, ExtractionResponse, RateLimited
from landed.core.schema import ConflictKind, QuoteState

PACK = Path(__file__).resolve().parents[1] / "packs" / "synthetic"
MANIFEST = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))


def field(value, page: int = 1) -> dict:
    return {"value": value, "page": page, "excerpt": str(value)}


# What each document actually says, keyed by the text the stub sees. Values mirror
# packs/synthetic/build.py; the stub stands in for a model reading them correctly.
QUOTES = {
    "A": dict(
        supplier_name="Shenzhen Precision Metalworks",
        currency="USD", incoterm="FOB Shenzhen", price="12.40", basis="per piece",
        moq=5000, tooling="8400", lead=35, weight="0.42",
    ),
    "B": dict(
        supplier_name="Guangzhou Hardline Industrial",
        currency="USD", incoterm="FOB Guangzhou", price="11.95", basis="per piece",
        moq=5000, tooling="12000", lead=42, weight="0.44",
    ),
    "C": dict(
        supplier_name="Ningbo Castworks",
        currency="USD", incoterm=None, price="11.20", basis="per piece",
        moq=8000, tooling="9500", lead=40, weight="0.41",
    ),
    "D": dict(
        supplier_name="Hanoi Precision Housing",
        currency="EUR", incoterm="DDP Karachi", price="11900", basis="per 1000 pieces",
        moq=10000, tooling="6000", lead=33, weight="0.43",
    ),
    "E": dict(
        supplier_name="Istanbul Metal Form",
        currency="USD", incoterm="CIF Karachi", price="13.60", basis="per piece",
        moq=3000, tooling="4200", lead=28, weight="0.45",
    ),
}


class PackStub:
    """Answers with whatever the document under the cursor actually states."""

    name = "stub"
    model = "stub-1"

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        blob = " ".join(request.document_text)
        supplier = self._identify(blob, request)
        is_profile = "Supplier Profile" in blob
        payload = self._profile(supplier) if is_profile else self._quote(supplier)
        return ExtractionResponse(
            payload=payload, provider=self.name, model_version=self.model
        )

    def _identify(self, blob: str, request: ExtractionRequest) -> str:
        for supplier_id, data in QUOTES.items():
            if data["supplier_name"].split()[0] in blob:
                return supplier_id
        return "E" if request.images else "A"   # the scanned quote has no text

    def _quote(self, supplier_id: str) -> dict:
        data = QUOTES[supplier_id]
        payload = {
            "supplier_name": field(data["supplier_name"]),
            "currency": field(data["currency"]),
            "line_items": [
                {
                    "unit_price": field(data["price"]),
                    "price_basis": field(data["basis"]),
                    "moq": field(data["moq"]),
                    "tooling_cost": field(data["tooling"]),
                    "lead_time_days": field(data["lead"]),
                    "unit_weight_kg": field(data["weight"]),
                }
            ],
        }
        if data["incoterm"]:
            payload["incoterm"] = field(data["incoterm"])
        return payload

    def _profile(self, supplier_id: str) -> dict:
        # Supplier B's profile states 10,000 where its quotation says 5,000.
        moq = 10_000 if supplier_id in {"B", "D"} else QUOTES[supplier_id]["moq"]
        return {
            "supplier_name": field(QUOTES[supplier_id]["supplier_name"]),
            "moq": field(moq),
            "lead_time_days": field(QUOTES[supplier_id]["lead"]),
        }


@pytest.fixture(scope="module")
def outcome():
    return compare_pack(PACK, quantity=10_000, provider=PackStub())


def expected_state(supplier_id: str) -> str:
    entry = next(s for s in MANIFEST["suppliers"] if s["supplier_id"] == supplier_id)
    return entry["expected_state"]


def find(outcome, supplier_id: str):
    return next(s for s in outcome.suppliers if s.supplier_id == supplier_id)


class TestGrouping:
    @pytest.mark.parametrize(
        ("filename", "supplier"),
        [
            ("quote_a.pdf", "A"),
            ("profile_b.docx", "B"),
            ("quote_d.xlsx", "D"),
            ("quote_e.png", "E"),
        ],
    )
    def test_supplier_is_read_from_the_filename(self, filename: str, supplier: str) -> None:
        assert supplier_id_from_filename(filename) == supplier

    def test_shared_documents_belong_to_no_supplier(self) -> None:
        for name in ("assumptions.xlsx", "bom.csv", "product_brief.docx"):
            assert supplier_id_from_filename(name) is None

    def test_quotes_and_profiles_land_in_the_same_bundle(self) -> None:
        from landed.core.ingest import ingest_pack

        grouped = group_by_supplier(ingest_pack(PACK))
        assert grouped["B"].quotations[0].filename == "quote_b.pdf"
        assert grouped["B"].profiles[0].filename == "profile_b.docx"


class TestAssumptions:
    def test_pack_assumptions_are_read_with_their_cells(self) -> None:
        from landed.core.ingest import ingest_file

        assumptions = load_assumptions(ingest_file(PACK / "assumptions.xlsx"))
        assert assumptions.base_currency == "USD"
        assert assumptions.freight_flat.value == Decimal("8200")
        assert assumptions.duty_rate.value == Decimal("0.065")
        assert assumptions.payment_days_outstanding == 60
        assert assumptions.fx_rate_date == "2026-07-14"
        assert assumptions.duty_rate.source.cell


class TestStates:
    @pytest.mark.parametrize("supplier_id", ["A", "B", "C", "D", "E"])
    def test_state_matches_the_manifest(self, outcome, supplier_id: str) -> None:
        assert find(outcome, supplier_id).state.value == expected_state(supplier_id)

    def test_contested_supplier_shows_both_moq_values(self, outcome) -> None:
        conflicts = find(outcome, "B").quotation.open_conflicts
        contradiction = next(c for c in conflicts if c.kind is ConflictKind.CONTRADICTION)
        assert set(contradiction.values) == {"5000", "10000"}

    def test_not_landed_supplier_names_what_is_missing(self, outcome) -> None:
        supplier = find(outcome, "C")
        assert supplier.refusal is not None
        assert "incoterm" in supplier.refusal.missing_fields
        assert supplier.breakdown is None

    def test_injection_is_flagged_without_blocking(self, outcome) -> None:
        supplier = find(outcome, "D")
        flags = [c for c in supplier.quotation.conflicts
                 if c.kind is ConflictKind.INJECTION_SUSPECTED]
        assert flags and flags[0].blocks_total is False
        assert supplier.state is QuoteState.LANDED

    def test_scanned_quote_is_flagged_for_verification(self, outcome) -> None:
        flags = [c for c in find(outcome, "E").quotation.conflicts
                 if c.kind is ConflictKind.VISION_READ]
        assert flags and all(f.blocks_total is False for f in flags)


class TestCosting:
    def test_landed_suppliers_carry_a_total(self, outcome) -> None:
        for supplier in outcome.landed:
            assert supplier.breakdown.total.value > 0
            assert supplier.breakdown.currency == "USD"

    def test_foreign_currency_quote_is_converted(self, outcome) -> None:
        """Supplier D quotes EUR per 1000; the dated rate makes it comparable."""
        breakdown = find(outcome, "D").breakdown
        assert breakdown.currency == "USD"
        # 11,900 EUR/1000 -> 11.90 EUR/piece -> x1.08 -> 12.852 USD x 10,000
        assert breakdown.goods.value == Decimal("128520.000")

    def test_ddp_supplier_bears_no_duty(self, outcome) -> None:
        assert find(outcome, "D").breakdown.duty.value == 0

    def test_ranking_excludes_suppliers_we_declined_to_cost(self, outcome) -> None:
        """Placing them last would read as 'most expensive', not 'not comparable'."""
        ranked = [s.supplier_id for s in outcome.ranked]
        assert "C" not in ranked
        assert "B" not in ranked

    def test_ranking_is_cheapest_first(self, outcome) -> None:
        per_unit = [s.breakdown.per_unit.value for s in outcome.ranked]
        assert per_unit == sorted(per_unit)

    def test_comparable_count_is_reported(self, outcome) -> None:
        assert outcome.comparable_count == "3 of 5"


class TestProviderFailure:
    """One document failing must not cost the work done on every other supplier."""

    class FlakyStub(PackStub):
        """Fails on the scanned quote, succeeds on everything else.

        Targets supplier E deliberately: an image has no text layer, so it is the
        one document that must reach a model and therefore the one that can be made
        to fail once the labelled pass handles the rest.
        """

        def extract(self, request):
            if request.images:
                raise RateLimited("429 quota exceeded")
            return super().extract(request)

    def test_a_rate_limited_supplier_does_not_abort_the_run(self) -> None:
        outcome = compare_pack(PACK, 10_000, provider=self.FlakyStub())
        assert len(outcome.landed) >= 2

    def test_the_failed_supplier_says_it_was_never_read(self) -> None:
        """Not 'unit price not stated' — that would be a lie about a document
        nobody managed to open."""
        outcome = compare_pack(PACK, 10_000, provider=self.FlakyStub())
        supplier = find(outcome, "E")
        assert "could not be read" in supplier.refusal.reason
        assert "rate limit" in supplier.refusal.reason

    def test_an_unread_supplier_is_not_landed_rather_than_contested(self) -> None:
        """Contested means two sources disagree. Nothing was read here."""
        outcome = compare_pack(PACK, 10_000, provider=self.FlakyStub())
        assert find(outcome, "E").state is QuoteState.NOT_LANDED

    def test_a_failure_is_marked_as_such_not_as_missing_data(self) -> None:
        outcome = compare_pack(PACK, 10_000, provider=self.FlakyStub())
        kinds = {c.kind for c in find(outcome, "E").quotation.conflicts}
        assert ConflictKind.EXTRACTION_FAILED in kinds
        assert find(outcome, "E").quotation.missing == []

    def test_labelled_documents_survive_a_dead_provider(self) -> None:
        """The point of reading deterministically: a spent quota no longer takes
        the whole comparison with it."""

        class DeadProvider:
            name, model = "dead", "none"

            def extract(self, request):
                raise RateLimited("429 quota exceeded")

        outcome = compare_pack(PACK, 10_000, provider=DeadProvider())
        assert {s.supplier_id for s in outcome.landed} >= {"A", "B", "D"} - {"B"}
        assert len(outcome.landed) >= 2


class TestRobustness:
    def test_unreadable_documents_are_reported(self, tmp_path: Path) -> None:
        from landed.core.ingest import ingest_file, ingest_pack
        from landed.core.pipeline import compare_documents

        broken = tmp_path / "quote_z.pdf"
        broken.write_bytes(b"not a pdf")
        documents = [*ingest_pack(PACK), ingest_file(broken)]
        result = compare_documents(documents, 10_000, PackStub())
        assert any("quote_z.pdf" in entry for entry in result.unreadable)

    def test_missing_assumptions_fails_loudly(self, tmp_path: Path) -> None:
        (tmp_path / "quote_a.pdf").write_bytes((PACK / "quote_a.pdf").read_bytes())
        with pytest.raises(ValueError, match="no assumptions document"):
            compare_pack(tmp_path, 10_000, PackStub())
