"""Assistant tests.

The two properties that make it safe to put in front of evidence: it cannot change
anything without a person confirming, and it produces no figures of its own.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from landed.core.providers import ExtractionRequest, ExtractionResponse, RateLimited
from landed.db.models import ChatMessage, Resolution, User
from landed.services import chat, comparisons, projects
from tests.test_pipeline import PACK, PackStub

DOCUMENTS = ["quote_a.pdf", "quote_c.pdf", "assumptions.xlsx"]


class ChatStub:
    """Returns a fixed reply and records the prompt it was given."""

    name, model = "stub", "stub-1"

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.payload = payload or {"answer": "Ningbo states no delivery terms.",
                                   "abstained": False, "action": {"kind": "none",
                                                                  "summary": ""}}
        self.error = error
        self.seen: ExtractionRequest | None = None

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.seen = request
        if self.error:
            raise self.error
        return ExtractionResponse(
            payload=self.payload, provider=self.name, model_version=self.model
        )


@pytest.fixture(autouse=True)
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        projects, "settings", lambda: SimpleNamespace(upload_dir=tmp_path)
    )
    return tmp_path


@pytest.fixture
def project(db: Session, user: User):
    created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
    for name in DOCUMENTS:
        projects.add_document(db, user, created.id, name, (PACK / name).read_bytes())
    comparisons.run_and_save(db, user, created.id, 10_000, provider=PackStub())
    return created


def supply_action(**overrides) -> dict:
    action = {
        "kind": "supply_value",
        "supplier_id": "C",
        "field_path": "incoterm",
        "value": "FOB Ningbo",
        "summary": "Record FOB Ningbo as the delivery term for Ningbo Castworks.",
    }
    return {**action, **overrides}


class TestAnswering:
    def test_both_turns_are_stored(self, db: Session, user: User, project) -> None:
        chat.ask(db, user, project.id, "Why is Ningbo blocked?", ChatStub())
        turns = chat.history(db, user, project.id)
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[0].content == "Why is Ningbo blocked?"

    def test_the_comparison_state_reaches_the_model(
        self, db: Session, user: User, project
    ) -> None:
        stub = ChatStub()
        chat.ask(db, user, project.id, "Why is Ningbo blocked?", stub)
        prompt = stub.seen.instruction
        assert "not_landed" in prompt
        assert "delivery terms (Incoterm) not stated" in prompt

    def test_no_comparison_yet_abstains_helpfully(
        self, db: Session, user: User
    ) -> None:
        empty = projects.create_project(db, user, "Empty")
        reply = chat.ask(db, user, empty.id, "What is cheapest?", ChatStub())
        assert "no comparison to read yet" in reply.content

    def test_the_assistant_states_its_own_rules(self) -> None:
        """Never calculate; answer only from the comparison."""
        assert "Never calculate" in chat.SYSTEM
        assert "Answer only from the comparison" in chat.SYSTEM


class TestProviderFailure:
    def test_a_rate_limit_is_reported_not_raised(
        self, db: Session, user: User, project
    ) -> None:
        stub = ChatStub(error=RateLimited("429 quota exceeded"))
        reply = chat.ask(db, user, project.id, "Why is Ningbo blocked?", stub)
        assert "unavailable" in reply.content
        assert reply.action is None

    def test_an_unreadable_reply_does_not_crash(
        self, db: Session, user: User, project
    ) -> None:
        stub = ChatStub(error=ValueError("model did not return valid JSON"))
        reply = chat.ask(db, user, project.id, "Anything?", stub)
        assert "unreadable" in reply.content


class TestProposalsAreInert:
    def test_a_proposal_changes_nothing_until_confirmed(
        self, db: Session, user: User, project
    ) -> None:
        """The confirmation is the approval step. Without it, a model's reading of
        a sentence would enter the audit trail unapproved."""
        payload = {"answer": "I can record that.", "abstained": False,
                   "action": supply_action()}
        reply = chat.ask(db, user, project.id, "Freight is FOB Ningbo", ChatStub(payload))
        assert reply.is_pending
        assert db.scalars(select(Resolution)).all() == []

    def test_confirming_records_it_through_the_normal_services(
        self, db: Session, user: User, project
    ) -> None:
        payload = {"answer": "I can record that.", "abstained": False,
                   "action": supply_action()}
        reply = chat.ask(db, user, project.id, "It is FOB Ningbo", ChatStub(payload))
        chat.confirm(db, user, project.id, reply.id)

        stored = db.scalars(select(Resolution)).one()
        assert stored.supplier_id == "C"
        assert stored.payload["value"] == "FOB Ningbo"
        assert stored.actor_email == user.email
        assert "assistant" in stored.rationale

    def test_confirming_twice_does_nothing_the_second_time(
        self, db: Session, user: User, project
    ) -> None:
        payload = {"answer": "ok", "abstained": False, "action": supply_action()}
        reply = chat.ask(db, user, project.id, "It is FOB Ningbo", ChatStub(payload))
        chat.confirm(db, user, project.id, reply.id)
        chat.confirm(db, user, project.id, reply.id)
        assert len(db.scalars(select(Resolution)).all()) == 1

    def test_an_incomplete_proposal_is_not_offered(
        self, db: Session, user: User, project
    ) -> None:
        """A supplier or field the model left blank cannot be confirmed into
        anything meaningful."""
        payload = {"answer": "ok", "abstained": False,
                   "action": supply_action(supplier_id=None)}
        reply = chat.ask(db, user, project.id, "Set it", ChatStub(payload))
        assert reply.action is None
        assert not reply.is_pending

    def test_a_recompute_proposal_runs_the_engine(
        self, db: Session, user: User, project
    ) -> None:
        """The assistant proposes; cost_engine produces the number."""
        payload = {"answer": "I can run that.", "abstained": False,
                   "action": {"kind": "recompute", "quantity": 50_000,
                              "summary": "Re-run at 50,000 units."}}
        reply = chat.ask(db, user, project.id, "What about 50,000?", ChatStub(payload))
        chat.confirm(db, user, project.id, reply.id, provider=PackStub())

        latest = comparisons.get_version(db, user, project.id)
        assert latest.quantity == 50_000
        assert latest.version == 2


class TestOwnership:
    def test_another_account_cannot_ask(self, db: Session, user: User, project) -> None:
        from landed.services import auth

        stranger = auth.register(db, "stranger@example.com", "correct-horse-battery")
        with pytest.raises(projects.ProjectNotFound):
            chat.ask(db, stranger, project.id, "What is cheapest?", ChatStub())

    def test_another_account_cannot_confirm(
        self, db: Session, user: User, project
    ) -> None:
        from landed.services import auth

        payload = {"answer": "ok", "abstained": False, "action": supply_action()}
        reply = chat.ask(db, user, project.id, "It is FOB", ChatStub(payload))
        stranger = auth.register(db, "stranger@example.com", "correct-horse-battery")
        with pytest.raises(projects.ProjectNotFound):
            chat.confirm(db, stranger, project.id, reply.id)
        assert db.scalars(select(Resolution)).all() == []

    def test_history_is_scoped_to_the_project(
        self, db: Session, user: User, project
    ) -> None:
        other = projects.create_project(db, user, "Unrelated")
        chat.ask(db, user, project.id, "Question one", ChatStub())
        assert chat.history(db, user, other.id) == []
        assert len(db.scalars(select(ChatMessage)).all()) == 2
