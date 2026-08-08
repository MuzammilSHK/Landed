"""Documents -> text with page anchors.

Deliberately dumb. This layer does no interpretation whatsoever — it only turns
bytes into text that still knows which page and sheet it came from. Every citation
in the finished product traces back to an anchor produced here, so losing position
information at this stage silently breaks provenance everywhere downstream.

Handles: PDF, XLSX, CSV, plain text.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Chunk(BaseModel):
    """A positioned piece of text from a source document."""

    text: str
    file: str
    page: int | None = None
    sheet: str | None = None


def ingest_file(path: Path) -> list[Chunk]:
    """Dispatch on file type and return positioned chunks.

    TODO: dispatch by suffix to the loaders below.
    """
    raise NotImplementedError


def ingest_pack(pack_dir: Path) -> dict[str, list[Chunk]]:
    """Ingest every readable document in a challenge pack.

    Returns a mapping of relative path -> chunks. Unreadable or unsupported files
    are reported rather than skipped silently — an unparsed quotation is a data
    gap the user needs to know about, not a file to ignore.

    TODO
    """
    raise NotImplementedError


def _load_pdf(path: Path) -> list[Chunk]:
    """pdfplumber, one Chunk per page. TODO"""
    raise NotImplementedError


def _load_xlsx(path: Path) -> list[Chunk]:
    """openpyxl, one Chunk per sheet. TODO"""
    raise NotImplementedError


def _load_csv(path: Path) -> list[Chunk]:
    """pandas, single Chunk. TODO"""
    raise NotImplementedError
