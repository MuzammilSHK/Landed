"""Documents in, three-state outcomes out.

The whole product in one function: read the pack, extract each supplier's quotation
and profile, detect what cannot be compared, cost what can, and return an outcome per
supplier. No persistence, no HTTP, no users — `services` adds those, and the
evaluation harness runs this directly with none of them.

Ordering matters and is deliberate. Every supplier is extracted before any is costed,
because two of the detectors need the whole field: a contradiction needs the profile
alongside the quotation, and a pricing-basis misread is only visible against peers.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .conflicts import annotate
from .cost_engine import compute
from .extract import extract_profile, extract_quotation
from .ingest import IngestedDocument, ingest_pack
from .packs import SupplierDocuments, find_assumptions, group_by_supplier, load_assumptions
from .providers import Provider, ProviderError, RateLimited, get_provider
from .resolutions import HumanResolution
from .resolutions import apply as apply_resolutions
from .schema import (
    Conflict,
    ConflictKind,
    CostAssumptions,
    CostBreakdown,
    CostResult,
    Quotation,
    QuoteState,
    Refusal,
    SupplierProfile,
)


class SupplierOutcome(BaseModel):
    """One supplier's position in the comparison."""

    model_config = ConfigDict(protected_namespaces=())

    supplier_id: str
    supplier_name: str | None = None
    quotation: Quotation
    profile: SupplierProfile | None = None
    result: CostResult
    raw_payloads: dict[str, dict] = Field(default_factory=dict)
    model_version: str | None = None

    @property
    def state(self) -> QuoteState:
        return self.quotation.state

    @property
    def breakdown(self) -> CostBreakdown | None:
        return self.result if isinstance(self.result, CostBreakdown) else None

    @property
    def refusal(self) -> Refusal | None:
        return self.result if isinstance(self.result, Refusal) else None


class ComparisonOutcome(BaseModel):
    """Everything a comparison produced, including what it declined to produce."""

    quantity: int
    currency: str
    assumptions: CostAssumptions
    suppliers: list[SupplierOutcome] = Field(default_factory=list)
    unreadable: list[str] = Field(default_factory=list)

    @property
    def landed(self) -> list[SupplierOutcome]:
        return [s for s in self.suppliers if s.state is QuoteState.LANDED]

    @property
    def ranked(self) -> list[SupplierOutcome]:
        """Cheapest first — landed suppliers only.

        A supplier we declined to cost is never ranked. Placing it last would read as
        'most expensive' rather than 'not yet comparable'.
        """
        return sorted(
            self.landed, key=lambda s: s.breakdown.per_unit.value  # type: ignore[union-attr]
        )

    @property
    def comparable_count(self) -> str:
        return f"{len(self.landed)} of {len(self.suppliers)}"


def compare_pack(
    pack_dir: Path,
    quantity: int,
    provider: Provider | None = None,
    assumptions: CostAssumptions | None = None,
    resolutions: list[HumanResolution] | None = None,
) -> ComparisonOutcome:
    """Read a pack directory and compare every supplier in it."""
    documents = ingest_pack(pack_dir)
    return compare_documents(documents, quantity, provider, assumptions, resolutions)


def compare_documents(
    documents: list[IngestedDocument],
    quantity: int,
    provider: Provider | None = None,
    assumptions: CostAssumptions | None = None,
    resolutions: list[HumanResolution] | None = None,
    supplier_map: dict[str, str] | None = None,
    document_kinds: dict[str, str] | None = None,
    declared: list[str] | None = None,
    names: dict[str, str] | None = None,
) -> ComparisonOutcome:
    """Compare every supplier across the supplied documents.

    `supplier_map`, `document_kinds`, and `declared` come from the web app, where the
    user has already said which supplier each file belongs to. Headless callers pass
    none of them and fall back to the pack filename convention.
    """
    engine = provider or get_provider()
    resolved = assumptions or _assumptions_from(documents)
    supplied = resolutions or []

    readable = [d for d in documents if d.is_readable]
    unreadable = [f"{d.filename}: {d.error}" for d in documents if not d.is_readable]

    bundles = group_by_supplier(readable, supplier_map, document_kinds, declared)

    # A supplier with no readable quotation is still part of the comparison. Dropping
    # it here is what previously made an unnamed file look like a supplier that never
    # existed; reporting it as NOT LANDED says the true thing instead — we know who
    # they are, and we have nothing to cost.
    extracted = [
        _extract_supplier(bundle, engine)
        for bundle in bundles.values()
        if bundle.is_costable
    ]

    quotations = [quotation for quotation, _, _, _ in extracted]
    suppliers = [
        _cost_supplier(
            quotation, profile, found, payloads, quotations, quantity, resolved, supplied
        )
        for quotation, profile, found, payloads in extracted
    ]
    suppliers.extend(
        _awaiting_quotation(bundle)
        for bundle in bundles.values()
        if not bundle.is_costable
    )

    for supplier in suppliers:
        supplier.supplier_name = (
            supplier.supplier_name or (names or {}).get(supplier.supplier_id)
        )

    return ComparisonOutcome(
        quantity=quantity,
        currency=resolved.base_currency,
        assumptions=resolved,
        suppliers=suppliers,
        unreadable=unreadable,
    )


def _awaiting_quotation(bundle: SupplierDocuments) -> SupplierOutcome:
    """A supplier on the list with nothing priced yet."""
    detail = (
        "a supplier profile was uploaded, but no quotation"
        if bundle.profiles
        else "no quotation has been uploaded"
    )
    quotation = Quotation(
        supplier_id=bundle.supplier_id,
        conflicts=[
            Conflict(
                kind=ConflictKind.MISSING_REQUIRED,
                field_path="document",
                message=f"Nothing to compare — {detail}.",
            )
        ],
        # NOT LANDED, not CONTESTED — nothing is in dispute, there is simply nothing
        # to read. `missing_fields` on the refusal stays empty so the UI does not
        # offer to have someone hand-type a quotation that should be uploaded.
        missing=["quotation"],
    )
    return SupplierOutcome(
        supplier_id=bundle.supplier_id,
        quotation=quotation,
        result=Refusal(
            supplier_id=bundle.supplier_id,
            reason=f"Nothing to compare — {detail}.",
            missing_fields=[],
            conflicts=quotation.conflicts,
        ),
    )


def _extract_supplier(
    bundle: SupplierDocuments, provider: Provider
) -> tuple[Quotation, SupplierProfile | None, list[Conflict], dict[str, dict]]:
    """Read one supplier's documents.

    A provider failure is recorded against that supplier rather than raised. Losing
    an entire comparison because one document hit a rate limit would throw away the
    work already done on every other supplier.
    """
    document = bundle.quotations[0]
    try:
        quotation, conflicts, payload = extract_quotation(
            document, bundle.supplier_id, provider
        )
    except ProviderError as failure:
        return (
            Quotation(supplier_id=bundle.supplier_id),
            None,
            [_unread_conflict(document.filename, failure)],
            {},
        )
    payloads = {document.filename: payload}

    profile: SupplierProfile | None = None
    for profile_document in bundle.profiles:
        try:
            profile, profile_conflicts, profile_payload = extract_profile(
                profile_document, bundle.supplier_id, provider
            )
        except ProviderError as failure:
            # The quotation was read; only the profile was not. Costing can still
            # proceed, but the contradiction check it would have enabled cannot.
            conflicts.append(_unread_conflict(profile_document.filename, failure))
            continue
        conflicts.extend(profile_conflicts)
        payloads[profile_document.filename] = profile_payload

    return quotation, profile, conflicts, payloads


def _unread_conflict(filename: str, failure: ProviderError) -> Conflict:
    kind = "rate limit" if isinstance(failure, RateLimited) else "extraction error"
    return Conflict(
        kind=ConflictKind.EXTRACTION_FAILED,
        field_path="document",
        message=f"{filename} could not be read ({kind}). Retry the comparison.",
        values=[str(failure)[:200]],
    )


def _cost_supplier(
    quotation: Quotation,
    profile: SupplierProfile | None,
    found: list[Conflict],
    payloads: dict[str, dict],
    peers: list[Quotation],
    quantity: int,
    assumptions: CostAssumptions,
    resolutions: list[HumanResolution],
) -> SupplierOutcome:
    if any(c.kind is ConflictKind.EXTRACTION_FAILED for c in found):
        # Skip detection entirely. Annotating a document nobody managed to open
        # would report every field as "not stated", which is a lie about it.
        unread = quotation.model_copy(update={"conflicts": found})
        return SupplierOutcome(
            supplier_id=quotation.supplier_id,
            quotation=unread,
            result=_refuse(unread),
            raw_payloads=payloads,
        )

    annotated = annotate(
        quotation,
        quantity,
        assumptions,
        profile=profile,
        peers=[q for q in peers if q.supplier_id != quotation.supplier_id],
        extra=found,
    )
    # Detection runs first, then human input answers it. Applying resolutions before
    # annotation would hide the conflict a person had already settled, leaving no
    # record on screen of what was in dispute.
    annotated, assumptions = apply_resolutions(annotated, assumptions, resolutions)

    result = (
        compute(annotated, quantity, assumptions)
        if annotated.state is QuoteState.LANDED
        else _refuse(annotated)
    )
    return SupplierOutcome(
        supplier_id=annotated.supplier_id,
        supplier_name=annotated.supplier_name,
        quotation=annotated,
        profile=profile,
        result=result,
        raw_payloads=payloads,
    )


def _refuse(quotation: Quotation) -> Refusal:
    """A contested or incomplete quote is never costed.

    Costing it anyway and hiding the total behind a badge would leave a number on
    screen that nobody should act on.
    """
    unread = [
        c for c in quotation.open_conflicts if c.kind is ConflictKind.EXTRACTION_FAILED
    ]
    if unread:
        reason = unread[0].message
    elif quotation.missing:
        reason = "cannot issue a total until these are supplied"
    else:
        reason = "sources disagree; no total until the conflict is resolved"
    return Refusal(
        supplier_id=quotation.supplier_id,
        reason=reason,
        missing_fields=quotation.missing,
        conflicts=quotation.open_conflicts,
    )


def _assumptions_from(
    documents: list[IngestedDocument], base_currency: str = "USD"
) -> CostAssumptions:
    """Read stated assumptions from the pack, or start from none.

    An absent assumptions sheet is not an error. Every field stays None, and the cost
    engine's own guard then reports freight and duty as missing inputs — a NOT LANDED
    naming exactly what is needed. Raising here instead threw away that answer and
    took the whole comparison down with it, which is the opposite of the behaviour
    this product is built to demonstrate.
    """
    document = find_assumptions(documents)
    if document is None:
        return CostAssumptions(base_currency=base_currency)
    return load_assumptions(document, base_currency)
