"""Reading a challenge pack's shape: which document belongs to whom, and what the
stated assumptions are.

This is the layer that will need rewriting when the official pack arrives, and it is
isolated here for exactly that reason. Everything downstream — extraction, conflict
detection, costing — works on the contract in `schema`, not on any pack's filenames
or spreadsheet layout. Adapting to a new pack should be an afternoon here, not a
rewrite anywhere else.

Assumption labels are matched on substrings rather than exact strings, because
"Ocean freight", "Freight (per container)" and "Sea freight rate" all mean the same
thing to a forwarder and none of them will match a constant.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .ingest import IngestedDocument
from .normalize import first_number
from .schema import CostAssumptions, Money, Source, Sourced

# Documents are attributed to a supplier by filename: quote_a.pdf and profile_a.docx
# both belong to supplier A. Packs that name files differently need only this regex
# changed, or an explicit mapping passed to `group_by_supplier`.
SUPPLIER_FROM_FILENAME = re.compile(
    r"^(?:quote|quotation|profile|supplier|offer)[_\-\s]*([A-Za-z0-9]+)", re.I
)

QUOTE_PREFIXES = ("quote", "quotation", "offer")
PROFILE_PREFIXES = ("profile", "supplier")

# Cell-tagged text from ingest: "B7=11900". One entry per populated cell.
CELL = re.compile(r"([A-Z]{1,3}\d+)=([^|]*)")


class SupplierDocuments:
    """Every document attributed to one supplier."""

    def __init__(self, supplier_id: str) -> None:
        self.supplier_id = supplier_id
        self.quotations: list[IngestedDocument] = []
        self.profiles: list[IngestedDocument] = []

    @property
    def is_costable(self) -> bool:
        """A profile alone describes a supplier but does not price anything."""
        return bool(self.quotations)

    def __repr__(self) -> str:
        return (
            f"SupplierDocuments({self.supplier_id!r}, "
            f"{len(self.quotations)} quote(s), {len(self.profiles)} profile(s))"
        )


def supplier_id_from_filename(filename: str) -> str | None:
    match = SUPPLIER_FROM_FILENAME.match(filename)
    return match.group(1).upper() if match else None


def group_by_supplier(
    documents: list[IngestedDocument],
    mapping: dict[str, str] | None = None,
) -> dict[str, SupplierDocuments]:
    """Attribute documents to suppliers.

    `mapping` overrides the filename convention for packs that do not follow it.
    Documents matching neither are shared pack material — briefs, BOMs, assumptions —
    and are returned to the caller's attention by being absent here rather than
    silently discarded.
    """
    grouped: dict[str, SupplierDocuments] = {}
    for document in documents:
        supplier_id = (mapping or {}).get(document.filename) or supplier_id_from_filename(
            document.filename
        )
        if supplier_id is None:
            continue
        bundle = grouped.setdefault(supplier_id, SupplierDocuments(supplier_id))
        lowered = document.filename.lower()
        if lowered.startswith(PROFILE_PREFIXES):
            bundle.profiles.append(document)
        elif lowered.startswith(QUOTE_PREFIXES):
            bundle.quotations.append(document)
    return dict(sorted(grouped.items()))


def find_assumptions(documents: list[IngestedDocument]) -> IngestedDocument | None:
    return next(
        (d for d in documents if "assumption" in d.filename.lower()), None
    )


def load_assumptions(
    document: IngestedDocument, base_currency: str = "USD"
) -> CostAssumptions:
    """Read the pack's stated logistics, duty, and currency assumptions.

    Only values the pack actually states are populated. Anything absent stays None,
    which is what makes the cost engine refuse rather than quietly assume a rate we
    were never given.
    """
    rows = _rows(document)
    assumptions = CostAssumptions(base_currency=_currency(rows, base_currency))

    freight_label, freight = _find(rows, "freight")
    per_kg = freight_label is not None and "per kg" in freight_label.unit.lower()

    return assumptions.model_copy(
        update={
            "freight_per_kg": freight if per_kg else None,
            "freight_flat": None if per_kg else freight,
            "duty_rate": _rate(rows, "duty"),
            "insurance_rate": _rate(rows, "insurance"),
            "financing_annual_rate": _rate(rows, "cost of capital", "financing"),
            "payment_days_outstanding": _int(rows, "payment days", "days outstanding"),
            "fx_rate_to_base": _rate(rows, " to ", "exchange rate", "fx rate to"),
            "fx_rate_date": _date(rows, "fx rate date", "rate date"),
        }
    )


class _Row:
    """One label/value/unit line from an assumptions sheet, with its cell reference."""

    def __init__(self, cells: dict[str, str], sheet: str | None, file: str) -> None:
        ordered = sorted(cells.items(), key=lambda pair: (len(pair[0]), pair[0]))
        values = [value for _, value in ordered]
        self.label = values[0] if values else ""
        self.value = values[1] if len(values) > 1 else ""
        self.unit = values[2] if len(values) > 2 else ""
        self.cell = ordered[1][0] if len(ordered) > 1 else (ordered[0][0] if ordered else "")
        self.sheet = sheet
        self.file = file

    def source(self) -> Source:
        return Source(
            file=self.file,
            sheet=self.sheet,
            cell=self.cell,
            excerpt=f"{self.label}: {self.value} {self.unit}".strip(),
        )


def _rows(document: IngestedDocument) -> list[_Row]:
    rows: list[_Row] = []
    for chunk in document.chunks:
        for line in chunk.text.splitlines():
            cells = {ref: text.strip() for ref, text in CELL.findall(line)}
            if len(cells) >= 2:
                rows.append(_Row(cells, chunk.sheet, chunk.file))
    return rows


def _find(rows: list[_Row], *needles: str) -> tuple[_Row | None, Money | None]:
    row = _match(rows, *needles)
    if row is None:
        return None, None
    amount = _decimal(row.value)
    if amount is None:
        return row, None
    return row, Money(value=amount, source=row.source())


def _match(rows: list[_Row], *needles: str) -> _Row | None:
    for needle in needles:
        for row in rows:
            if needle.lower() in row.label.lower():
                return row
    return None


def _rate(rows: list[_Row], *needles: str) -> Sourced[Decimal] | None:
    row = _match(rows, *needles)
    if row is None:
        return None
    amount = _decimal(row.value)
    return None if amount is None else Sourced[Decimal](value=amount, source=row.source())


def _int(rows: list[_Row], *needles: str) -> int:
    row = _match(rows, *needles)
    if row is None:
        return 0
    amount = _decimal(row.value)
    return 0 if amount is None else int(amount)


def _date(rows: list[_Row], *needles: str) -> str | None:
    row = _match(rows, *needles)
    return row.value.strip() if row and row.value.strip() else None


def _currency(rows: list[_Row], fallback: str) -> str:
    row = _match(rows, "base currency", "reporting currency")
    value = (row.value.strip().upper() if row else "") or fallback
    return value[:3]


def _decimal(text: str) -> Decimal | None:
    return first_number(text)
