"""The extraction contract.

This is the single source of truth every other module obeys. Extraction maps
arbitrary supplier documents *onto* these shapes; it never invents new ones. If the
challenge pack uses different column names, that is the adapter's problem, not this
file's.

Two rules encoded here structurally rather than by convention:

1. There are no bare values. Every extracted datum is a `Field`, which carries its
   own provenance. A float cannot enter the system without a source.
2. `Origin` distinguishes a fact read from a document, an assumption we introduced,
   and something a model produced. The brief requires these be separable at
   evaluation time, so they are separated at the type level.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field as PydanticField


# --------------------------------------------------------------------------- #
# Provenance primitives
# --------------------------------------------------------------------------- #

class Origin(str, Enum):
    """Where a value came from. Never collapse these into one category."""

    EXTRACTED = "extracted"      # read from a supplied document
    ASSUMED = "assumed"          # introduced by us, must be declared
    DERIVED = "derived"          # computed by cost_engine from other Fields
    MODEL = "model"              # produced by an LLM; never used in arithmetic


class PriceBasis(str, Enum):
    PER_PIECE = "per_piece"
    PER_1000 = "per_1000"
    PER_KG = "per_kg"
    LOT = "lot"                  # flat price for the whole order


class QuoteState(str, Enum):
    """The product's core state model. See README."""

    LANDED = "landed"            # complete and sourced — total issued
    CONTESTED = "contested"      # sources disagree — both shown, no winner
    NOT_LANDED = "not_landed"    # required field missing — no total issued


class Source(BaseModel):
    """Pointer back to the exact place a value was read from."""

    file: str
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    excerpt: str | None = None   # short verbatim snippet for the UI drawer


class Field(BaseModel):
    """A value that knows where it came from.

    Nothing in Landed is allowed to be a naked number. `cost_engine` rejects any
    input that is not a Field, which is what makes the provenance guarantee hold
    at runtime rather than by discipline.
    """

    value: Decimal | str | int | None
    unit: str | None = None
    currency: str | None = None
    origin: Origin = Origin.EXTRACTED
    source: Source | None = None
    confidence: float | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #

class ConflictKind(str, Enum):
    MISSING_REQUIRED = "missing_required"
    CONTRADICTION = "contradiction"          # two sources, two values
    UNIT_MISMATCH = "unit_mismatch"          # per_piece vs per_1000, kg vs lb
    UNDATED_CURRENCY = "undated_currency"    # FX with no date to convert at
    STALE_QUOTE = "stale_quote"              # past its validity window
    INJECTION_SUSPECTED = "injection_suspected"


class Conflict(BaseModel):
    """A reason a comparison cannot yet be trusted.

    These are first-class outputs, not error logs. They drive the UI panel that is
    the product's headline.
    """

    kind: ConflictKind
    field_path: str                      # e.g. "line_items[0].moq"
    message: str                         # human-readable, shown verbatim in the UI
    sources: list[Source] = PydanticField(default_factory=list)
    values: list[str] = PydanticField(default_factory=list)
    blocks_total: bool = True            # does this prevent issuing a cost?


# --------------------------------------------------------------------------- #
# Quotation
# --------------------------------------------------------------------------- #

class LineItem(BaseModel):
    part_number: Field | None = None
    description: Field | None = None
    unit_price: Field | None = None
    price_basis: Field | None = None
    quantity: Field | None = None
    moq: Field | None = None
    tooling_cost: Field | None = None
    lead_time_days: Field | None = None


class Quotation(BaseModel):
    """One supplier's quote, normalized onto the contract."""

    supplier_id: str
    quote_id: str | None = None
    line_items: list[LineItem] = PydanticField(default_factory=list)

    incoterm: Field | None = None        # FOB / DDP / EXW + named place
    currency: Field | None = None
    quote_date: Field | None = None
    validity_days: Field | None = None
    payment_terms: Field | None = None
    origin_country: Field | None = None

    # Populated by conflicts.py, not by extraction.
    missing: list[str] = PydanticField(default_factory=list)
    conflicts: list[Conflict] = PydanticField(default_factory=list)

    @property
    def state(self) -> QuoteState:
        """TODO: derive from `missing` and `conflicts`.

        missing non-empty              -> NOT_LANDED
        any conflict with blocks_total -> CONTESTED
        otherwise                      -> LANDED
        """
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Cost output
# --------------------------------------------------------------------------- #

class CostBreakdown(BaseModel):
    """Itemized landed cost. Judges need to see the arithmetic to trust the total,
    so the breakdown is the primary output and the total is a convenience."""

    goods: Field
    tooling_amortized: Field
    freight: Field
    duty: Field
    insurance: Field
    financing: Field
    total: Field
    currency: str
    quantity: int
    assumptions: list[Field] = PydanticField(default_factory=list)


class Refusal(BaseModel):
    """Returned instead of a total when required inputs are absent.

    This is the fallback case the brief requires demonstrated. It is a deliberate
    product behaviour, not an exception path.
    """

    supplier_id: str
    reason: str
    missing_fields: list[str]
    conflicts: list[Conflict] = PydanticField(default_factory=list)


CostResult = CostBreakdown | Refusal


# Fields without which no total may be issued. Referenced by cost_engine's guard
# clause and by conflicts.detect_missing.
REQUIRED_FOR_TOTAL: tuple[str, ...] = (
    "unit_price",
    "price_basis",
    "quantity",
    "currency",
    "incoterm",
)
