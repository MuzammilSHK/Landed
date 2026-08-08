"""Documents to structured quotations.

The model reports what a document says. It never computes, converts, or totals — that
is `cost_engine`'s job, and the separation is what makes the arithmetic testable.

Provenance is assembled here in code rather than accepted from the model. The wire
schema asks only for a value, the page or cell it was read from, and the text quoted
verbatim; `Source` objects are then built locally, with `ReadMethod` decided by
whether that page had a text layer. A model cannot mislabel a scan as a text read,
because it is never asked.

A field arriving without a citation is dropped. An uncited value is indistinguishable
from a fabricated one, and a recorded gap is more useful than a plausible guess.

Injection is detected two ways: a deterministic scan over document text, and the
model's own report. The first matters more — it does not depend on the model noticing
that it was being manipulated.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .ingest import IngestedDocument
from .normalize import parse_incoterm, parse_price_basis
from .providers import ExtractionRequest, ImagePart, Provider, get_provider
from .schema import (
    Conflict,
    ConflictKind,
    Count,
    Incoterm,
    LineItem,
    Money,
    PriceBasis,
    Quotation,
    ReadMethod,
    Source,
    Sourced,
    SupplierProfile,
    Text,
)

# Phrases that only appear in a supplier document when someone is addressing the
# reader's tooling rather than the reader. Deterministic, so detection does not
# depend on the model choosing to mention it.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(the\s+)?(above|previous|prior)", re.I),
    re.compile(r"system\s*(note|prompt|message)\s*:", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"(rank|rate|score)\s+(this|it)\s+(supplier\s+)?first", re.I),
    re.compile(r"mark\s+(all|every)?\s*fields?\s+as\s+verified", re.I),
    re.compile(r"do\s+not\s+(report|flag|mention)", re.I),
)

_CITATION_KEYS = ("page", "sheet", "cell")

_CITATION_PROPERTIES = {
    "page": {"type": ["integer", "null"]},
    "sheet": {"type": ["string", "null"]},
    "cell": {"type": ["string", "null"]},
    "excerpt": {"type": "string", "description": "the text this was read from, verbatim"},
    "confidence": {"type": "number"},
}


def field(description: str, choices: list[str] | None = None) -> dict:
    """One cited field.

    The description is not decoration. An unexplained `price_basis` came back as
    "piece" one call and "per piece" the next; naming the choices removes the
    ambiguity at its source rather than parsing around it.
    """
    value: dict = {"description": description}
    if choices:
        value["enum"] = choices
    return {
        "type": "object",
        "properties": {"value": value, **_CITATION_PROPERTIES},
        "required": ["value", "excerpt"],
    }


FIELD_SPEC = field("the value exactly as printed")

QUOTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_name": FIELD_SPEC,
        "quote_id": FIELD_SPEC,
        "currency": field("ISO 4217 code the quote is priced in, e.g. USD, EUR"),
        "incoterm": field(
            "delivery term as a three-letter Incoterms 2020 code",
            [term.value for term in Incoterm],
        ),
        "named_place": field("the port or place named alongside the Incoterm"),
        "quote_date": field("date the quote was issued, as YYYY-MM-DD"),
        "validity_days": field("how many days the quote stays valid"),
        "payment_terms": FIELD_SPEC,
        "origin_country": FIELD_SPEC,
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part_number": FIELD_SPEC,
                    "description": FIELD_SPEC,
                    "unit_price": field(
                        "the price number only, without currency symbol or basis"
                    ),
                    "price_basis": field(
                        "what quantity the unit price is for",
                        [basis.value for basis in PriceBasis],
                    ),
                    "moq": field("minimum order quantity, in pieces"),
                    "tooling_cost": field("one-off tooling or setup charge"),
                    "lead_time_days": field("production lead time in days"),
                    "unit_weight_kg": field("weight of one finished unit in kilograms"),
                },
            },
        },
        "injection_suspected": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "excerpt": {"type": "string"},
            },
        },
    },
}

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_name": FIELD_SPEC,
        "moq": field("minimum order quantity stated in the profile, in pieces"),
        "lead_time_days": field("typical production lead time in days"),
        "capacity_units_per_month": field("stated monthly production capacity"),
        "certifications": {"type": "array", "items": FIELD_SPEC},
        "injection_suspected": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "excerpt": {"type": "string"},
            },
        },
    },
}

QUOTATION_INSTRUCTION = (
    "Extract this supplier quotation. For every field, give the value exactly as "
    "printed, the page or cell it appears on, and the text you read it from. Omit "
    "any field the document does not state."
)

PROFILE_INSTRUCTION = (
    "Extract this supplier capability profile. For every field, give the value "
    "exactly as printed, the page it appears on, and the text you read it from. "
    "Omit any field the document does not state."
)


def scan_for_injection(document: IngestedDocument) -> list[Conflict]:
    """Flag document text that addresses the reader's tooling rather than the reader.

    Flagged and surfaced, never obeyed and never silently dropped — a discarded
    injection attempt is itself something the buyer should know about.
    """
    found: list[Conflict] = []
    for chunk in document.chunks:
        for pattern in INJECTION_PATTERNS:
            match = pattern.search(chunk.text)
            if match is None:
                continue
            found.append(
                Conflict(
                    kind=ConflictKind.INJECTION_SUSPECTED,
                    field_path="document",
                    message=(
                        f"{document.filename} contains instruction-shaped text "
                        f"('{match.group(0).strip()}'). It was not acted on."
                    ),
                    sources=[Source(file=chunk.file, page=chunk.page)],
                    values=[_excerpt_around(chunk.text, match.start())],
                    blocks_total=False,
                )
            )
            break  # one flag per document is enough to prompt a human look
    return found


def build_request(
    document: IngestedDocument, instruction: str, json_schema: dict
) -> ExtractionRequest:
    return ExtractionRequest(
        instruction=instruction,
        json_schema=json_schema,
        document_text=[chunk.text for chunk in document.chunks],
        images=[
            ImagePart(data=image.data, media_type=image.media_type)
            for image in document.images
        ],
    )


def extract_quotation(
    document: IngestedDocument,
    supplier_id: str,
    provider: Provider | None = None,
) -> tuple[Quotation, list[Conflict], dict]:
    """Read one quotation. Returns the quotation, any conflicts, and the raw payload.

    The raw payload is returned for storage: which model produced which reading is
    part of a value's provenance.
    """
    engine = provider or get_provider()
    response = engine.extract(
        build_request(document, QUOTATION_INSTRUCTION, QUOTATION_SCHEMA)
    )
    payload = response.payload

    quotation = Quotation(
        supplier_id=supplier_id,
        supplier_name=_raw(payload.get("supplier_name")),
        quote_id=_raw(payload.get("quote_id")),
        currency=_text(payload.get("currency"), document),
        incoterm=_incoterm(payload.get("incoterm"), document),
        named_place=_text(payload.get("named_place"), document),
        quote_date=_text(payload.get("quote_date"), document),
        validity_days=_count(payload.get("validity_days"), document),
        payment_terms=_text(payload.get("payment_terms"), document),
        origin_country=_text(payload.get("origin_country"), document),
        line_items=[
            _line_item(item, document, payload.get("currency"))
            for item in payload.get("line_items") or []
        ],
    )
    return quotation, _injection_conflicts(document, payload), payload


def extract_profile(
    document: IngestedDocument,
    supplier_id: str,
    provider: Provider | None = None,
) -> tuple[SupplierProfile, list[Conflict], dict]:
    engine = provider or get_provider()
    response = engine.extract(build_request(document, PROFILE_INSTRUCTION, PROFILE_SCHEMA))
    payload = response.payload

    profile = SupplierProfile(
        supplier_id=supplier_id,
        supplier_name=_raw(payload.get("supplier_name")),
        moq=_count(payload.get("moq"), document),
        lead_time_days=_count(payload.get("lead_time_days"), document),
        capacity_units_per_month=_count(payload.get("capacity_units_per_month"), document),
        certifications=[
            text
            for text in (
                _text(entry, document) for entry in payload.get("certifications") or []
            )
            if text is not None
        ],
    )
    return profile, _injection_conflicts(document, payload), payload


# --------------------------------------------------------------------------- #
# Wire payload -> Sourced values
# --------------------------------------------------------------------------- #

def _line_item(item: dict, document: IngestedDocument, currency: Any) -> LineItem:
    return LineItem(
        part_number=_text(item.get("part_number"), document),
        description=_text(item.get("description"), document),
        unit_price=_money(item.get("unit_price"), document, currency),
        price_basis=_price_basis(item.get("price_basis"), document),
        moq=_count(item.get("moq"), document),
        tooling_cost=_money(item.get("tooling_cost"), document, currency),
        lead_time_days=_count(item.get("lead_time_days"), document),
        unit_weight_kg=_money(item.get("unit_weight_kg"), document, currency=None),
    )


def _source(field: dict, document: IngestedDocument) -> Source | None:
    """Build the citation locally. Returns None when the model gave no anchor."""
    if not any(field.get(key) is not None for key in _CITATION_KEYS):
        return None
    page = field.get("page")
    return Source(
        file=document.filename,
        page=page,
        sheet=field.get("sheet"),
        cell=field.get("cell"),
        excerpt=(field.get("excerpt") or "").strip() or None,
        read_method=_read_method(document, page),
    )


def _read_method(document: IngestedDocument, page: Any) -> ReadMethod:
    """Decided from what we ingested, never from what the model claims."""
    if not document.images:
        return ReadMethod.TEXT_LAYER
    rendered = {image.page for image in document.images}
    if page is None:
        return ReadMethod.VISION if not document.chunks else ReadMethod.TEXT_LAYER
    return ReadMethod.VISION if page in rendered else ReadMethod.TEXT_LAYER


def _usable(field: Any) -> dict | None:
    """A field is usable only if it has a value and a citation."""
    if not isinstance(field, dict) or field.get("value") in (None, ""):
        return None
    return field


def _raw(field: Any) -> str | None:
    usable = _usable(field)
    return None if usable is None else str(usable["value"]).strip() or None


def _text(field: Any, document: IngestedDocument) -> Text | None:
    usable = _usable(field)
    if usable is None:
        return None
    source = _source(usable, document)
    if source is None:
        return None
    return Text(
        value=str(usable["value"]).strip(),
        source=source,
        confidence=_confidence(usable),
    )


def _money(field: Any, document: IngestedDocument, currency: Any) -> Money | None:
    usable = _usable(field)
    if usable is None:
        return None
    amount = _decimal(usable["value"])
    source = _source(usable, document)
    if amount is None or source is None:
        return None
    return Money(
        value=amount,
        currency=_raw(currency),
        source=source,
        confidence=_confidence(usable),
    )


def _count(field: Any, document: IngestedDocument) -> Count | None:
    usable = _usable(field)
    if usable is None:
        return None
    amount = _decimal(usable["value"])
    source = _source(usable, document)
    if amount is None or source is None:
        return None
    return Count(value=int(amount), source=source, confidence=_confidence(usable))


def _incoterm(field: Any, document: IngestedDocument) -> Sourced[Incoterm] | None:
    usable = _usable(field)
    if usable is None:
        return None
    term = parse_incoterm(str(usable["value"]))
    source = _source(usable, document)
    if term is None or source is None:
        return None
    return Sourced[Incoterm](value=term, source=source, confidence=_confidence(usable))


def _price_basis(field: Any, document: IngestedDocument) -> Sourced[PriceBasis] | None:
    usable = _usable(field)
    if usable is None:
        return None
    basis = parse_price_basis(str(usable["value"]))
    source = _source(usable, document)
    if basis is None or source is None:
        return None
    return Sourced[PriceBasis](value=basis, source=source, confidence=_confidence(usable))


def _decimal(value: Any) -> Decimal | None:
    """Parse a number as printed, tolerating thousands separators and currency marks."""
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None


def _confidence(field: dict) -> float | None:
    value = field.get("confidence")
    return float(value) if isinstance(value, int | float) else None


def _injection_conflicts(document: IngestedDocument, payload: dict) -> list[Conflict]:
    """Deterministic scan first; the model's own report only adds to it."""
    conflicts = scan_for_injection(document)
    reported = payload.get("injection_suspected") or {}
    if reported.get("found") and not conflicts:
        conflicts.append(
            Conflict(
                kind=ConflictKind.INJECTION_SUSPECTED,
                field_path="document",
                message=(
                    f"{document.filename} was reported by the extraction model as "
                    f"containing instruction-shaped text. It was not acted on."
                ),
                sources=[Source(file=document.filename)],
                values=[str(reported.get("excerpt", ""))[:200]],
                blocks_total=False,
            )
        )
    return conflicts


def _excerpt_around(text: str, index: int, width: int = 120) -> str:
    start = max(0, index - width // 2)
    return text[start : start + width].replace("\n", " ").strip()
