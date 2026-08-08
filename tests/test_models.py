"""Persistence model tests.

Two properties matter more than the rest: one user's work must be unreachable from
another account, and a comparison version must never be overwritten — a report sent
last week has to still say what it said.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from landed.db.models import (
    Comparison,
    ComparisonResult,
    Document,
    Extraction,
    Project,
    Resolution,
    User,
)


def comparison(project_id: int, version: int = 1) -> Comparison:
    return Comparison(
        project_id=project_id,
        version=version,
        quantity=10_000,
        currency="USD",
        assumptions={"duty_rate": "0.065"},
    )


def document(project_id: int, name: str = "quote_a.pdf") -> Document:
    return Document(
        project_id=project_id,
        filename=name,
        kind="quotation",
        supplier_id="A",
        sha256="a" * 64,
        byte_size=2048,
        stored_path=f"uploads/{name}",
    )


class TestAccounts:
    def test_email_is_unique(self, db: Session, user: User) -> None:
        db.add(User(email=user.email, password_hash="other"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_email_is_case_insensitive(self, db: Session, user: User) -> None:
        """CITEXT: one address, however it was typed. Enforced by the database, not
        by every call site remembering to lowercase."""
        db.add(User(email="BUYER@Example.COM", password_hash="other"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_lookup_ignores_capitalisation(self, db: Session, user: User) -> None:
        found = db.scalars(select(User).where(User.email == "Buyer@Example.com")).one()
        assert found.id == user.id

    def test_projects_belong_to_their_owner(self, db: Session, project: Project) -> None:
        loaded = db.get(Project, project.id)
        assert loaded is not None
        assert loaded.owner.email == "buyer@example.com"


class TestOwnershipIsolation:
    def test_another_users_projects_are_not_returned(
        self, db: Session, project: Project
    ) -> None:
        """The query the web layer must always scope by. Verified here, not assumed."""
        intruder = User(email="someone@else.com", password_hash="x")
        db.add(intruder)
        db.commit()

        visible = db.scalars(select(Project).where(Project.user_id == intruder.id)).all()
        assert visible == []

    def test_deleting_a_user_removes_their_projects(
        self, db: Session, user: User, project: Project
    ) -> None:
        db.delete(user)
        db.commit()
        assert db.get(Project, project.id) is None


class TestForeignKeys:
    def test_orphan_document_is_rejected(self, db: Session) -> None:
        db.add(document(project_id=9999))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_deleting_a_project_cascades_to_documents(
        self, db: Session, project: Project
    ) -> None:
        db.add(document(project.id))
        db.commit()
        db.delete(project)
        db.commit()
        assert db.scalars(select(Document)).all() == []


class TestVersioning:
    def test_version_is_unique_within_a_project(self, db: Session, project: Project) -> None:
        db.add_all([comparison(project.id, 1), comparison(project.id, 1)])
        with pytest.raises(IntegrityError):
            db.commit()

    def test_versions_accumulate_rather_than_replace(
        self, db: Session, project: Project
    ) -> None:
        db.add_all([comparison(project.id, v) for v in (1, 2, 3)])
        db.commit()
        versions = db.scalars(
            select(Comparison.version).where(Comparison.project_id == project.id)
        ).all()
        assert sorted(versions) == [1, 2, 3]

    def test_same_version_is_allowed_in_a_different_project(
        self, db: Session, user: User, project: Project
    ) -> None:
        other = Project(user_id=user.id, name="Q4 Housing")
        db.add(other)
        db.commit()
        db.add_all([comparison(project.id, 1), comparison(other.id, 1)])
        db.commit()
        assert len(db.scalars(select(Comparison)).all()) == 2


class TestResults:
    def test_a_refusal_is_stored_as_a_result_not_an_absent_row(
        self, db: Session, project: Project
    ) -> None:
        """'We declined to cost this' is information the report must carry."""
        run = comparison(project.id)
        db.add(run)
        db.commit()
        db.add(
            ComparisonResult(
                comparison_id=run.id,
                supplier_id="C",
                state="not_landed",
                refusal={"reason": "freight terms not stated"},
                conflicts=[],
            )
        )
        db.commit()
        stored = db.scalars(select(ComparisonResult)).one()
        assert stored.state == "not_landed"
        assert stored.breakdown is None
        assert stored.refusal["reason"] == "freight terms not stated"


class TestExtractions:
    def test_re_extraction_adds_a_row_rather_than_overwriting(
        self, db: Session, project: Project
    ) -> None:
        """Which model read a value is part of its provenance."""
        doc = document(project.id)
        db.add(doc)
        db.commit()
        db.add_all(
            [
                Extraction(
                    document_id=doc.id, payload={}, provider="anthropic", model_version="v1"
                ),
                Extraction(
                    document_id=doc.id, payload={}, provider="gemini", model_version="v2"
                ),
            ]
        )
        db.commit()
        assert {e.provider for e in db.get(Document, doc.id).extractions} == {
            "anthropic",
            "gemini",
        }


class TestResolutions:
    def test_a_supplied_assumption_is_recorded_with_its_author(
        self, db: Session, project: Project
    ) -> None:
        db.add(
            Resolution(
                project_id=project.id,
                supplier_id="C",
                field_path="freight",
                kind="assumption",
                payload={"value": "2400", "currency": "USD"},
                actor_email="buyer@example.com",
                rationale="confirmed with supplier by email",
            )
        )
        db.commit()
        stored = db.scalars(select(Resolution)).one()
        assert stored.actor_email == "buyer@example.com"
        assert stored.is_active

    def test_reverting_keeps_the_record(self, db: Session, project: Project) -> None:
        """Reversal must not erase what was once assumed."""
        entry = Resolution(
            project_id=project.id,
            supplier_id="C",
            field_path="freight",
            kind="assumption",
            payload={"value": "2400"},
            actor_email="buyer@example.com",
        )
        db.add(entry)
        db.commit()
        entry.reverted_at = datetime.now(UTC)
        db.commit()
        assert not entry.is_active
        assert db.scalars(select(Resolution)).one() is not None
