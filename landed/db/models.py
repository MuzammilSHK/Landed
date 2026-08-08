"""Persistence models.

Domain objects are stored as JSONB rather than shredded into columns. A `Quotation`
carries provenance on every value; flattening it would either lose that or require a
table per field. The relational structure earns its place where we query — ownership,
project history, version lineage — and JSONB holds the evidence, indexed where the
dashboard needs to filter on it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CITEXT so Buyer@Example.com and buyer@example.com are one account, enforced by
    # the database rather than by every call site remembering to normalise.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()

    projects: Mapped[list[Project]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Project(Base):
    """A sourcing decision in progress: its documents, its comparisons, its history."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    product_name: Mapped[str | None] = mapped_column(String(200))
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    destination_country: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    documents: Mapped[list[Document]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    comparisons: Mapped[list[Comparison]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    resolutions: Mapped[list[Resolution]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    chat_messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    # The dashboard's only listing query: this user's projects, most recent first.
    __table_args__ = (Index("ix_projects_user_updated", "user_id", "updated_at"),)


class Document(Base):
    """An uploaded source file.

    `sha256` is what lets a report say which bytes produced a number, and lets a
    re-upload of an unchanged file be recognised rather than re-extracted.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(400))
    content_type: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="quotation")
    supplier_id: Mapped[str | None] = mapped_column(String(80), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(String(600))
    uploaded_at: Mapped[datetime] = _created_at()

    project: Mapped[Project] = relationship(back_populates="documents")
    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Extraction(Base):
    """One model's reading of one document.

    Kept separate from `Document` so a re-extraction with a different model is a new
    row rather than an overwrite — which model read a value is part of its provenance.
    """

    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    payload: Mapped[dict] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(40))
    model_version: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()

    document: Mapped[Document] = relationship(back_populates="extractions")


class Comparison(Base):
    """One costed run of a project, versioned.

    Versions are never overwritten: a report sent last week must still say what it
    said, and the diff between two versions is the product's answer to whether the
    decision moved.
    """

    __tablename__ = "comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    assumptions: Mapped[dict] = mapped_column(JSONB)
    document_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created_at()

    project: Mapped[Project] = relationship(back_populates="comparisons")
    results: Mapped[list[ComparisonResult]] = relationship(
        back_populates="comparison", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_comparison_project_version"),
    )


class ComparisonResult(Base):
    """One supplier's outcome within a comparison.

    Exactly one of `breakdown` or `refusal` is populated. A refusal is a result, not
    an absent row — "we declined to cost this" is information the report must carry.
    """

    __tablename__ = "comparison_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE")
    )
    supplier_id: Mapped[str] = mapped_column(String(80))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), index=True)
    breakdown: Mapped[dict | None] = mapped_column(JSONB)
    refusal: Mapped[dict | None] = mapped_column(JSONB)
    conflicts: Mapped[list] = mapped_column(JSONB, default=list)

    comparison: Mapped[Comparison] = relationship(back_populates="results")

    # GIN over the conflict payload turns "every project still blocked on an MOQ
    # disagreement" into an indexed lookup instead of a scan over every result.
    __table_args__ = (
        Index("ix_results_conflicts_gin", "conflicts", postgresql_using="gin"),
    )


class ChatMessage(Base):
    """One turn of the assistant conversation for a project.

    Stored rather than kept in the session so an assumption recorded from a
    conversation is still readable alongside the exchange that produced it.

    `action` holds a proposal the assistant made. It is inert: nothing changes until
    a person confirms it, at which point a Resolution is written.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))        # user | assistant
    content: Mapped[str] = mapped_column(Text)
    action: Mapped[dict | None] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    project: Mapped[Project] = relationship(back_populates="chat_messages")

    @property
    def is_pending(self) -> bool:
        return bool(self.action) and self.confirmed_at is None


class Resolution(Base):
    """A human decision: a supplied assumption or a chosen authoritative source.

    The audit trail behind every value the documents did not provide. Reversible by
    design — `reverted_at` rather than a delete, so the record of what was once
    assumed survives the reversal.
    """

    __tablename__ = "resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    supplier_id: Mapped[str] = mapped_column(String(80))
    field_path: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))          # assumption | source_choice
    payload: Mapped[dict] = mapped_column(JSONB)
    actor_email: Mapped[str] = mapped_column(CITEXT)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="resolutions")

    @property
    def is_active(self) -> bool:
        return self.reverted_at is None
