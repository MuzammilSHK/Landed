"""Reading labelled documents without a model.

A great deal of quotation content is labelled — "Unit price: USD 12.40 per piece",
"MOQ: 5,000 pieces" — and spreadsheets are structured already. None of that needs a
language model, and every field read here is one the model is not asked about: fewer
calls, no quota spent, no network, and a citation derived from the exact line rather
than from something a model reported.

This is a first pass, not a replacement. Labels vary, layouts vary, and a scan has no
text at all; `extract` falls back to the model for whatever is still missing. The
patterns match on *label wording*, deliberately, not on position or on any particular
document's shape — a parser tuned to the pack we happen to have would read beautifully
here and collapse on the organizer's, which is the testbed shortcut the brief warns
about.

Output is the same wire shape the model returns, so the mapping, validation, and
citation rules downstream are identical whichever pass produced a field.
"""

from __future__ import annotations

import re

from .ingest import Chunk, IngestedDocument

# "A5=Price | B5=11900 | C5=EUR per 1000 pieces" — how ingest renders a spreadsheet row.
CELL_LINE = re.compile(r"([A-Z]{1,3}\d+)=([^|]*)")

# "Unit price:            USD 12.40 per piece"
LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z ()/.'-]{2,40}?)\s*[:\-]\s+(.+?)\s*$")

QUOTATION_LABELS: dict[str, tuple[str, ...]] = {
    "quote_id": ("quotation ref", "quote ref", "quotation no", "quote no", "reference"),
    "quote_date": ("^date$", "quotation date", "date of issue", "issued"),
    "validity_days": ("validity", "valid for", "valid until"),
    "incoterm": ("delivery terms", "incoterm", "shipping terms", "trade terms"),
    "payment_terms": ("payment terms", "terms of payment"),
    "origin_country": ("country of origin", "^origin$", "made in"),
    "unit_price": ("unit price", "price per", "^price$", "unit cost", "ex-works price"),
    "moq": ("minimum order", "^moq$", "min order", "min. order"),
    "tooling_cost": ("tooling", "one-off", "one off", "setup charge", "mould", "mold"),
    "lead_time_days": ("lead time", "production time", "delivery time"),
    "unit_weight_kg": ("unit weight", "weight per", "^weight$", "net weight"),
    "part_number": ("^part$", "part number", "part no", "item code"),
}

PROFILE_LABELS: dict[str, tuple[str, ...]] = {
    "moq": ("minimum order", "^moq$", "min order"),
    "lead_time_days": ("lead time", "production time", "typical production"),
    "capacity_units_per_month": ("capacity", "monthly output"),
}

CURRENCY = re.compile(r"\b(USD|EUR|GBP|CNY|RMB|JPY|INR|PKR|AED|SGD|CHF|CAD|AUD)\b")

# "QUOTATION - Shenzhen Precision Metalworks", "Supplier Profile: Hanoi Precision".
# The supplier's name is almost never a labelled field; it is the heading.
HEADING = re.compile(
    r"^\s*(?:quotation|quote|supplier\s+profile|profile|offer|proforma[\w ]*)"
    # Hyphen, en dash, em dash, colon — real documents use all four. Escaped so the
    # dashes are unmistakable in source.
    "\\s*[-\u2013\u2014:]\\s*(.+?)\\s*$",
    re.I,
)


class _Line:
    """One label/value pair with enough context to cite it."""

    def __init__(self, label: str, value: str, chunk: Chunk, cell: str | None) -> None:
        self.label = label.strip()
        self.value = value.strip()
        self.chunk = chunk
        self.cell = cell
        self.text = f"{self.label}: {self.value}"

    def field(self, value: str | None = None) -> dict:
        """The wire shape the model would have returned for this field."""
        return {
            "value": value if value is not None else self.value,
            "page": self.chunk.page,
            "sheet": self.chunk.sheet,
            "cell": self.cell,
            "excerpt": self.text[:160],
            "confidence": 1.0,
        }


def read_quotation(document: IngestedDocument) -> dict:
    """Everything a quotation states in labelled form. Absent fields are simply absent."""
    lines = _lines(document)
    if not lines:
        return {}

    found = _match_all(lines, QUOTATION_LABELS)
    payload: dict = {}
    item: dict = {}

    name = _heading_name(document)
    if name is not None:
        payload["supplier_name"] = name

    for name in ("quote_id", "quote_date", "validity_days", "payment_terms",
                 "origin_country", "incoterm"):
        if name in found:
            payload[name] = found[name].field()

    for name in ("unit_price", "moq", "tooling_cost", "lead_time_days",
                 "unit_weight_kg", "part_number"):
        if name in found:
            item[name] = found[name].field()

    price = found.get("unit_price")
    if price is not None:
        # The basis and the currency are usually printed on the price line itself,
        # which is also the only place they can be read without guessing.
        item["price_basis"] = price.field(price.value)
        currency = CURRENCY.search(price.value) or _search_currency(lines)
        if currency:
            payload["currency"] = price.field(currency.group(1))

    if item:
        payload["line_items"] = [item]
    return payload


def read_profile(document: IngestedDocument) -> dict:
    lines = _lines(document)
    payload = {name: line.field() for name, line in _match_all(lines, PROFILE_LABELS).items()}
    name = _heading_name(document)
    if name is not None:
        payload["supplier_name"] = name
    return payload


def _heading_name(document: IngestedDocument) -> dict | None:
    """The supplier's name, taken from the document heading.

    Checked against a short list of document-type words rather than "the first line",
    so a letterhead or a page number does not become a supplier name.
    """
    for chunk in document.chunks:
        for raw in chunk.text.splitlines()[:6]:
            match = HEADING.match(raw)
            if match and len(match.group(1)) > 2:
                return {
                    "value": match.group(1).strip(),
                    "page": chunk.page,
                    "sheet": chunk.sheet,
                    "cell": None,
                    "excerpt": raw.strip()[:160],
                    "confidence": 1.0,
                }
    return None


def missing_from(payload: dict, required: tuple[str, ...]) -> list[str]:
    """Which required fields this pass did not produce.

    Drives the decision to call a model at all: nothing missing, no call.
    """
    item = (payload.get("line_items") or [{}])[0]
    return [
        name
        for name in required
        if payload.get(name) is None and item.get(name) is None
    ]


def merge(primary: dict, secondary: dict) -> dict:
    """Combine two passes, preferring `primary` field by field.

    The deterministic pass wins where it found something: it read the literal text of
    a line, so its citation points at that line rather than at a page a model
    nominated.
    """
    combined = {**secondary, **{k: v for k, v in primary.items() if k != "line_items"}}
    primary_item = (primary.get("line_items") or [{}])[0]
    secondary_item = (secondary.get("line_items") or [{}])[0]
    merged_item = {**secondary_item, **primary_item}
    if merged_item:
        combined["line_items"] = [merged_item]
    return combined


def _lines(document: IngestedDocument) -> list[_Line]:
    lines: list[_Line] = []
    for chunk in document.chunks:
        for raw in chunk.text.splitlines():
            cells = CELL_LINE.findall(raw)
            if len(cells) >= 2:
                label, value = cells[0][1], cells[1][1]
                unit = cells[2][1] if len(cells) > 2 else ""
                joined = f"{value} {unit}".strip()
                lines.append(_Line(label, joined, chunk, cells[1][0]))
                continue
            match = LABEL_LINE.match(raw)
            if match:
                lines.append(_Line(match.group(1), match.group(2), chunk, None))
    return lines


def _match_all(lines: list[_Line], labels: dict[str, tuple[str, ...]]) -> dict[str, _Line]:
    """First match wins per field: a quotation states its price once, and a later
    mention is usually a footnote rather than a correction."""
    found: dict[str, _Line] = {}
    for line in lines:
        lowered = line.label.lower()
        for name, patterns in labels.items():
            if name in found:
                continue
            if any(re.search(pattern, lowered) for pattern in patterns):
                found[name] = line
                break
    return found


def _search_currency(lines: list[_Line]):
    for line in lines:
        match = CURRENCY.search(line.value)
        if match:
            return match
    return None
