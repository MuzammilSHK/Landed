"""The extraction contract.

Two guarantees are enforced structurally here rather than by convention:

1. No bare values. Every datum is a `Sourced[T]` carrying its own provenance, so a
   number cannot enter the system without saying where it came from.
2. Facts, assumptions, and derived values stay distinguishable, because the brief
   requires them separable at evaluation time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class Origin(str, Enum):
    """Where a value came from. Never collapse these into one category."""

    EXTRACTED = "extracted"   # read from a supplied document
    ASSUMED = "assumed"       # supplied by a person or an assumptions file
    DERIVED = "derived"       # computed by cost_engine from other values
    MODEL = "model"           # produced by an LLM; never used in arithmetic


class ReadMethod(str, Enum):
    """How a document was read. Vision-derived values warrant verification."""

    TEXT_LAYER = "text_layer"
    VISION = "vision"


class PriceBasis(str, Enum):
    PER_PIECE = "per_piece"
    PER_1000 = "per_1000"
    PER_KG = "per_kg"
    LOT = "lot"               # flat price for the whole order


class Incoterm(str, Enum):
    """Incoterms 2020.

    Which legs the buyer bears is the whole reason two identical unit prices can
    differ by thousands, so the responsibility split lives on the term itself.
    """

    EXW = "EXW"
    FCA = "FCA"
    FAS = "FAS"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"
    CPT = "CPT"
    CIP = "CIP"
    DAP = "DAP"
    DPU = "DPU"
    DDP = "DDP"

    @property
    def buyer_bears_freight(self) -> bool:
        """Seller arranges main carriage from CFR/CPT onward."""
        return self in {Incoterm.EXW, Incoterm.FCA, Incoterm.FAS, Incoterm.FOB}

    @property
    def buyer_bears_insurance(self) -> bool:
        """Only CIF and CIP oblige the seller to insure."""
        return self not in {Incoterm.CIF, Incoterm.CIP, Incoterm.DAP,
                            Incoterm.DPU, Incoterm.DDP}

    @property
    def buyer_bears_duty(self) -> bool:
        """DDP is the only term placing import clearance on the seller."""
        return self is not Incoterm.DDP


class QuoteState(str, Enum):
    """The product's core state model."""

    LANDED = "landed"          # complete and sourced — total issued
    CONTESTED = "contested"    # sources disagree — both shown, neither chosen
    NOT_LANDED = "not_landed"  # required field missing — no total issued


class Source(BaseModel):
    """The exact place in a document where a value was read."""

    model_config = ConfigDict(frozen=True)

    file: str
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    excerpt: str | None = None
    read_method: ReadMethod = ReadMethod.TEXT_LAYER


class Attribution(BaseModel):
    """Who supplied a value that no document states."""

    model_config = ConfigDict(frozen=True)

    actor: str
    at: datetime
    rationale: str | None = None


class Sourced(BaseModel, Generic[T]):
    """A value that knows where it came from.

    An extracted value without a source is treated as not found: an uncited number
    is indistinguishable from a fabricated one.
    """

    value: T
    unit: str | None = None
    currency: str | None = None
    origin: Origin = Origin.EXTRACTED
    source: Source | None = None
    attribution: Attribution | None = None
    confidence: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _require_provenance(self) -> Sourced[T]:
        if self.origin is Origin.EXTRACTED and self.source is None:
            raise ValueError("extracted values require a source")
        if self.origin is Origin.ASSUMED and not (self.attribution or self.source):
            raise ValueError("assumed values require an attribution or a source")
        return self

    @property
    def needs_verification(self) -> bool:
        """True when read from an image rather than a machine-readable text layer."""
        return self.source is not None and self.source.read_method is ReadMethod.VISION


Money = Sourced[Decimal]
Count = Sourced[int]
Text = Sourced[str]


class ConflictKind(str, Enum):
    MISSING_REQUIRED = "missing_required"
    CONTRADICTION = "contradiction"          # two sources, two values
    UNIT_MISMATCH = "unit_mismatch"          # per_piece against per_1000
    UNDATED_CURRENCY = "undated_currency"    # foreign currency, no rate date
    UNKNOWN_INCOTERM = "unknown_incoterm"
    STALE_QUOTE = "stale_quote"
    VISION_READ = "vision_read"              # advisory: verify against the image
    INJECTION_SUSPECTED = "injection_suspected"
    EXTRACTION_FAILED = "extraction_failed"  # the document was never successfully read


class Conflict(BaseModel):
    """A reason a comparison cannot yet be trusted.

    First-class output, not an error log — these drive the panel the product is
    actually about.
    """

    kind: ConflictKind
    field_path: str
    message: str                              # shown verbatim in the UI
    sources: list[Source] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    blocks_total: bool = True
    resolved_with: Attribution | None = None

    @property
    def is_open(self) -> bool:
        return self.resolved_with is None


class LineItem(BaseModel):
    part_number: Text | None = None
    description: Text | None = None
    unit_price: Money | None = None
    price_basis: Sourced[PriceBasis] | None = None
    moq: Count | None = None
    tooling_cost: Money | None = None
    lead_time_days: Count | None = None
    unit_weight_kg: Money | None = None


class Quotation(BaseModel):
    """One supplier's quote, mapped onto the contract."""

    supplier_id: str
    supplier_name: str | None = None
    quote_id: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)

    incoterm: Sourced[Incoterm] | None = None
    named_place: Text | None = None
    currency: Text | None = None
    quote_date: Text | None = None
    validity_days: Count | None = None
    payment_terms: Text | None = None
    origin_country: Text | None = None

    # Populated by conflicts.detect_all, not by extraction.
    missing: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)

    @property
    def state(self) -> QuoteState:
        # A document nobody managed to read is NOT LANDED, not CONTESTED. Contested
        # means two sources disagree, and claiming that about a file we never opened
        # would misdescribe the problem to the person who has to fix it.
        if self.missing or any(
            c.kind is ConflictKind.EXTRACTION_FAILED and c.is_open for c in self.conflicts
        ):
            return QuoteState.NOT_LANDED
        if any(c.blocks_total and c.is_open for c in self.conflicts):
            return QuoteState.CONTESTED
        return QuoteState.LANDED

    @property
    def open_conflicts(self) -> list[Conflict]:
        return [c for c in self.conflicts if c.is_open]


class SupplierProfile(BaseModel):
    """A supplier's own capability document.

    Exists to be disagreed with. Profiles routinely restate terms the quotation also
    states, and when the two differ the honest answer is to show both rather than to
    prefer whichever was parsed second.
    """

    supplier_id: str
    supplier_name: str | None = None
    moq: Count | None = None
    lead_time_days: Count | None = None
    capacity_units_per_month: Count | None = None
    certifications: list[Text] = Field(default_factory=list)


class CostAssumptions(BaseModel):
    """Inputs the cost engine needs that the quotation itself does not state.

    Populated by a pack adapter from the organizer's stated logistics, duty, and
    currency assumptions. Deliberately flat: keeping lookup logic in the adapter
    leaves the engine pack-agnostic and unit-testable.

    Every field is `Sourced`, so an assumption can never be mistaken in the UI for
    something a supplier actually quoted.
    """

    base_currency: str
    fx_rate_to_base: Sourced[Decimal] | None = None  # None when already in base
    fx_rate_date: str | None = None                  # absent => conversion refused
    freight_flat: Money | None = None
    freight_per_kg: Money | None = None
    duty_rate: Sourced[Decimal] | None = None          # 0.065 == 6.5%
    insurance_rate: Sourced[Decimal] | None = None
    financing_annual_rate: Sourced[Decimal] | None = None
    payment_days_outstanding: int = 0

    def all_sourced(self) -> list[Sourced]:
        """Every assumption actually supplied, for display and export."""
        candidates = (
            self.fx_rate_to_base, self.freight_flat, self.freight_per_kg,
            self.duty_rate, self.insurance_rate, self.financing_annual_rate,
        )
        return [c for c in candidates if c is not None]


class CostBreakdown(BaseModel):
    """Itemized landed cost.

    The breakdown is the product; the total is a convenience. Judges need to see the
    arithmetic to trust the number.
    """

    supplier_id: str
    quantity: int
    currency: str
    goods: Money
    tooling_amortized: Money
    freight: Money
    duty: Money
    insurance: Money
    financing: Money
    total: Money
    per_unit: Money
    assumptions: list[Sourced] = Field(default_factory=list)
    advisories: list[Conflict] = Field(default_factory=list)


class Refusal(BaseModel):
    """Returned instead of a total when required inputs are absent.

    A designed product behaviour, not an exception path — inventing a freight term
    to produce a tidy number is the failure this project exists to prevent.
    """

    supplier_id: str
    reason: str
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


CostResult = CostBreakdown | Refusal


# Fields without which no total may be issued. Referenced by the cost engine's
# guard and by conflicts.detect_missing so the two cannot drift apart.
REQUIRED_FOR_TOTAL: tuple[str, ...] = (
    "unit_price",
    "price_basis",
    "currency",
    "incoterm",
)
