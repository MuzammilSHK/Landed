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
from .providers import Provider, get_provider
from .resolutions import HumanResolution
from .resolutions import apply as apply_resolutions
from .schema import (
    Conflict,
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
) -> ComparisonOutcome:
    engine = provider or get_provider()
    resolved = assumptions or _assumptions_from(documents)
    supplied = resolutions or []

    readable = [d for d in documents if d.is_readable]
    unreadable = [f"{d.filename}: {d.error}" for d in documents if not d.is_readable]

    extracted = [
        _extract_supplier(bundle, engine)
        for bundle in group_by_supplier(readable).values()
        if bundle.is_costable
    ]

    quotations = [quotation for quotation, _, _, _ in extracted]
    suppliers = [
        _cost_supplier(
            quotation, profile, found, payloads, quotations, quantity, resolved, supplied
        )
        for quotation, profile, found, payloads in extracted
    ]

    return ComparisonOutcome(
        quantity=quantity,
        currency=resolved.base_currency,
        assumptions=resolved,
        suppliers=suppliers,
        unreadable=unreadable,
    )


def _extract_supplier(
    bundle: SupplierDocuments, provider: Provider
) -> tuple[Quotation, SupplierProfile | None, list[Conflict], dict[str, dict]]:
    document = bundle.quotations[0]
    quotation, conflicts, payload = extract_quotation(
        document, bundle.supplier_id, provider
    )
    payloads = {document.filename: payload}

    profile: SupplierProfile | None = None
    for profile_document in bundle.profiles:
        profile, profile_conflicts, profile_payload = extract_profile(
            profile_document, bundle.supplier_id, provider
        )
        conflicts.extend(profile_conflicts)
        payloads[profile_document.filename] = profile_payload

    return quotation, profile, conflicts, payloads


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
    reason = (
        "cannot issue a total until these are supplied"
        if quotation.missing
        else "sources disagree; no total until the conflict is resolved"
    )
    return Refusal(
        supplier_id=quotation.supplier_id,
        reason=reason,
        missing_fields=quotation.missing,
        conflicts=quotation.open_conflicts,
    )


def _assumptions_from(documents: list[IngestedDocument]) -> CostAssumptions:
    document = find_assumptions(documents)
    if document is None:
        raise ValueError(
            "no assumptions document found in the pack; supply one explicitly"
        )
    return load_assumptions(document)
