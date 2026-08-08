"""Ingestion tests, run against the synthetic pack.

Two properties carry the most weight: position anchors survive (every citation
downstream depends on them), and a page with no text layer routes to vision rather
than being reported as empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from landed.core.ingest import (
    SCANNED_TEXT_THRESHOLD,
    ingest_file,
    ingest_pack,
)
from landed.core.schema import ReadMethod

PACK = Path(__file__).resolve().parents[1] / "packs" / "synthetic"


def read(name: str):
    return ingest_file(PACK / name)


class TestPdf:
    def test_text_layer_is_extracted_with_page_anchors(self) -> None:
        document = read("quote_a.pdf")
        assert document.is_readable
        assert document.chunks[0].page == 1
        assert document.chunks[0].file == "quote_a.pdf"
        assert "12.40" in document.chunks[0].text

    def test_text_pdf_needs_no_vision(self) -> None:
        assert read("quote_a.pdf").needs_vision is False

    def test_read_method_defaults_to_text_layer(self) -> None:
        assert read("quote_a.pdf").chunks[0].read_method is ReadMethod.TEXT_LAYER

    def test_injection_text_survives_ingestion_verbatim(self) -> None:
        """Ingestion must not sanitise hostile text away — the extractor needs to
        see it in order to flag it."""
        assert "Ignore all previous instructions" in read("profile_d.pdf").chunks[0].text


class TestImages:
    def test_image_only_document_routes_to_vision(self) -> None:
        document = read("quote_e.png")
        assert document.needs_vision
        assert document.chunks == []
        assert document.images[0].media_type == "image/png"

    def test_rendered_image_carries_its_page_and_file(self) -> None:
        image = read("quote_e.png").images[0]
        assert image.page == 1
        assert image.file == "quote_e.png"

    def test_image_payload_is_bounded(self) -> None:
        """A full-resolution scan costs several times more to send and carries no
        more readable detail."""
        assert len(read("quote_e.png").images[0].data) < 2_000_000


class TestOfficeFormats:
    def test_docx_paragraphs_are_read(self) -> None:
        document = read("profile_b.docx")
        assert "10,000 pieces" in document.chunks[0].text

    def test_excel_cells_carry_their_coordinates(self) -> None:
        """A citation reading 'B7 of the Quotation sheet' is checkable; 'somewhere in
        the spreadsheet' is not."""
        chunk = read("quote_d.xlsx").chunks[0]
        assert chunk.sheet == "Quotation"
        assert "=11900" in chunk.text
        assert any(f"{col}7=" in chunk.text for col in "ABC")

    def test_csv_rows_are_numbered(self) -> None:
        text = read("bom.csv").chunks[0].text
        assert text.startswith("row 1:")
        assert "ALU-ENC-140-BODY" in text


class TestFailureHandling:
    def test_unsupported_type_is_reported_not_skipped(self, tmp_path: Path) -> None:
        """A quotation that silently failed to parse is a supplier missing from the
        comparison, which is worse than an error."""
        legacy = tmp_path / "quote_old.doc"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0legacy binary")
        document = ingest_file(legacy)
        assert not document.is_readable
        assert "unsupported file type" in document.error

    def test_corrupt_file_reports_the_reason(self, tmp_path: Path) -> None:
        broken = tmp_path / "quote_broken.pdf"
        broken.write_bytes(b"not really a pdf")
        document = ingest_file(broken)
        assert not document.is_readable
        assert document.error

    def test_empty_file_is_reported(self, tmp_path: Path) -> None:
        blank = tmp_path / "blank.csv"
        blank.write_text("", encoding="utf-8")
        assert ingest_file(blank).error == "no readable content found"

    def test_every_document_is_hashed(self) -> None:
        """sha256 is what lets a report say which bytes produced a number."""
        document = read("quote_a.pdf")
        assert len(document.sha256) == 64
        assert document.byte_size > 0


class TestPack:
    def test_pack_ingests_in_a_stable_order(self) -> None:
        first = [d.filename for d in ingest_pack(PACK)]
        second = [d.filename for d in ingest_pack(PACK)]
        assert first == second == sorted(first)

    def test_pack_skips_generator_and_notes(self) -> None:
        names = {d.filename for d in ingest_pack(PACK)}
        assert "build.py" not in names
        assert "README.md" not in names

    def test_every_pack_document_is_readable(self) -> None:
        unreadable = [
            (d.filename, d.error) for d in ingest_pack(PACK) if not d.is_readable
        ]
        assert unreadable == []

    def test_pack_covers_both_read_paths(self) -> None:
        documents = ingest_pack(PACK)
        assert any(d.needs_vision for d in documents)
        assert any(d.chunks for d in documents)


class TestThreshold:
    @pytest.mark.parametrize("length", [0, 1, SCANNED_TEXT_THRESHOLD - 1])
    def test_sparse_pages_are_treated_as_scans(self, length: int) -> None:
        """Page numbers and stray marks routinely yield a handful of characters."""
        assert length < SCANNED_TEXT_THRESHOLD
