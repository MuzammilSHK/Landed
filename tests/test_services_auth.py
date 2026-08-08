"""Authentication and ownership tests.

The two properties worth proving: a password never survives in a readable form, and
one account cannot reach another's work. Both are the sort of thing that passes
review by inspection and fails in practice.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from landed.db.models import User
from landed.services import auth, projects

PASSWORD = "correct-horse-battery"


class TestPasswordStorage:
    def test_password_is_not_stored_in_readable_form(self, db: Session) -> None:
        user = auth.register(db, "buyer@example.com", PASSWORD)
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2")

    def test_same_password_hashes_differently_each_time(self, db: Session) -> None:
        """Per-password salt: identical passwords must not produce identical hashes."""
        a = auth.register(db, "one@example.com", PASSWORD)
        b = auth.register(db, "two@example.com", PASSWORD)
        assert a.password_hash != b.password_hash

    def test_verification_accepts_the_right_password(self) -> None:
        assert auth.verify_password(PASSWORD, auth.hash_password(PASSWORD))

    def test_verification_rejects_the_wrong_one(self) -> None:
        assert not auth.verify_password("nearly-right", auth.hash_password(PASSWORD))

    def test_malformed_hash_is_rejected_rather_than_raising(self) -> None:
        assert not auth.verify_password(PASSWORD, "not-a-hash")


class TestRegistration:
    def test_duplicate_email_is_refused(self, db: Session) -> None:
        auth.register(db, "buyer@example.com", PASSWORD)
        with pytest.raises(auth.EmailAlreadyRegistered):
            auth.register(db, "buyer@example.com", PASSWORD)

    def test_duplicate_is_caught_by_the_database_not_a_prior_check(self, db: Session) -> None:
        """Check-then-insert leaves a race two concurrent registrations both win."""
        auth.register(db, "buyer@example.com", PASSWORD)
        with pytest.raises(auth.EmailAlreadyRegistered):
            auth.register(db, "BUYER@example.com", PASSWORD)

    def test_short_password_is_refused(self, db: Session) -> None:
        with pytest.raises(auth.WeakPassword):
            auth.register(db, "buyer@example.com", "short")

    def test_session_is_usable_after_a_rejected_registration(self, db: Session) -> None:
        """A rolled-back failure must not poison the session for the next call."""
        auth.register(db, "buyer@example.com", PASSWORD)
        with pytest.raises(auth.EmailAlreadyRegistered):
            auth.register(db, "buyer@example.com", PASSWORD)
        assert auth.register(db, "other@example.com", PASSWORD).id


class TestAuthentication:
    def test_correct_credentials_return_the_user(self, db: Session) -> None:
        registered = auth.register(db, "buyer@example.com", PASSWORD)
        assert auth.authenticate(db, "buyer@example.com", PASSWORD).id == registered.id

    def test_capitalisation_of_the_address_does_not_matter(self, db: Session) -> None:
        auth.register(db, "buyer@example.com", PASSWORD)
        assert auth.authenticate(db, "Buyer@Example.COM", PASSWORD) is not None

    def test_wrong_password_returns_none(self, db: Session) -> None:
        auth.register(db, "buyer@example.com", PASSWORD)
        assert auth.authenticate(db, "buyer@example.com", "wrong-password") is None

    def test_unknown_address_returns_none(self, db: Session) -> None:
        assert auth.authenticate(db, "nobody@example.com", PASSWORD) is None

    def test_unknown_address_looks_like_a_bad_password(self, db: Session) -> None:
        """Same return value, and the dummy verification keeps the cost comparable."""
        auth.register(db, "buyer@example.com", PASSWORD)
        assert auth.authenticate(db, "buyer@example.com", "wrong") is None
        assert auth.authenticate(db, "nobody@example.com", PASSWORD) is None


class TestOwnership:
    @pytest.fixture
    def stranger(self, db: Session) -> User:
        return auth.register(db, "stranger@example.com", PASSWORD)

    def test_a_project_is_reachable_by_its_owner(self, db: Session, user: User) -> None:
        created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
        assert projects.get_project(db, user, created.id).id == created.id

    def test_another_users_project_is_not_reachable(
        self, db: Session, user: User, stranger: User
    ) -> None:
        created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
        with pytest.raises(projects.ProjectNotFound):
            projects.get_project(db, stranger, created.id)

    def test_forbidden_is_indistinguishable_from_missing(
        self, db: Session, user: User, stranger: User
    ) -> None:
        """Otherwise the error tells a prober which project ids exist."""
        created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
        with pytest.raises(projects.ProjectNotFound):
            projects.get_project(db, stranger, created.id)
        with pytest.raises(projects.ProjectNotFound):
            projects.get_project(db, stranger, 999_999)

    def test_listing_shows_only_your_own(
        self, db: Session, user: User, stranger: User
    ) -> None:
        projects.create_project(db, user, "Mine")
        projects.create_project(db, stranger, "Theirs")
        assert [p.name for p in projects.list_projects(db, user)] == ["Mine"]

    def test_another_user_cannot_delete_your_project(
        self, db: Session, user: User, stranger: User
    ) -> None:
        created = projects.create_project(db, user, "Q3 Enclosure Sourcing")
        with pytest.raises(projects.ProjectNotFound):
            projects.delete_project(db, stranger, created.id)
        assert projects.get_project(db, user, created.id)


class TestDocuments:
    @pytest.fixture(autouse=True)
    def upload_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(
            projects, "settings", lambda: SimpleNamespace(upload_dir=tmp_path)
        )
        return tmp_path

    def test_uploaded_filename_cannot_shape_the_stored_path(
        self, db: Session, user: User, upload_dir: Path
    ) -> None:
        """An uploaded name is attacker-controlled input; traversal must not escape."""
        project = projects.create_project(db, user, "Q3")
        document = projects.add_document(
            db, user, project.id, "../../etc/passwd.pdf", b"%PDF-1.4 fake"
        )
        assert document.filename == "passwd.pdf"
        assert Path(document.stored_path).is_relative_to(upload_dir)

    def test_content_is_stored_under_its_hash(
        self, db: Session, user: User, upload_dir: Path
    ) -> None:
        project = projects.create_project(db, user, "Q3")
        document = projects.add_document(db, user, project.id, "quote.pdf", b"%PDF fake")
        assert Path(document.stored_path).stem == document.sha256
        assert Path(document.stored_path).read_bytes() == b"%PDF fake"

    def test_two_suppliers_sending_the_same_filename_do_not_collide(
        self, db: Session, user: User
    ) -> None:
        project = projects.create_project(db, user, "Q3")
        first = projects.add_document(db, user, project.id, "quotation.pdf", b"supplier A")
        second = projects.add_document(db, user, project.id, "quotation.pdf", b"supplier B")
        assert first.stored_path != second.stored_path

    def test_documents_belong_to_their_project(self, db: Session, user: User) -> None:
        stranger = auth.register(db, "stranger@example.com", PASSWORD)
        project = projects.create_project(db, user, "Q3")
        with pytest.raises(projects.ProjectNotFound):
            projects.add_document(db, stranger, project.id, "quote.pdf", b"data")
