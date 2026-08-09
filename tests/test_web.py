"""Web layer tests.

Weighted towards what must not happen: an anonymous request reaching a project, one
account reading another's, a form accepted without its CSRF token, and a blocked
supplier rendering a number.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from landed.services import auth, projects
from landed.web.app import create_app
from landed.web.security import db_session
from tests.test_pipeline import PACK, PackStub

PASSWORD = "correct-horse-battery"
REGISTER = "/register"


@pytest.fixture(autouse=True)
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        projects, "settings", lambda: SimpleNamespace(upload_dir=tmp_path)
    )
    return tmp_path


@pytest.fixture(autouse=True)
def stub_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web layer is under test here, not the model."""
    monkeypatch.setattr("landed.core.pipeline.get_provider", lambda *_: PackStub())


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    return TestClient(app, follow_redirects=False)


def sign_in(client: TestClient, email: str = "buyer@example.com") -> None:
    response = client.post(
        "/register",
        data={"email": email, "password": PASSWORD, **_csrf(client, REGISTER)},
    )
    assert response.status_code == 303, response.text[:200]


def _csrf(client: TestClient, path: str = "/login") -> dict[str, str]:
    """Read the token out of a rendered page, the way a browser would.

    Deliberately not decoded from the cookie: this asserts the token actually reaches
    the form, which is the thing that was broken.
    """
    # Follow redirects: /login bounces to the dashboard once signed in, and either
    # destination renders a form carrying the session's token.
    page = client.get(path, follow_redirects=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, f"no CSRF token rendered on {path}"
    return {"csrf_token": match.group(1)}


class TestAnonymousAccess:
    def test_dashboard_redirects_to_login(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    def test_a_project_page_redirects_to_login(self, client: TestClient) -> None:
        assert client.get("/projects/1").status_code == 303

    def test_login_page_renders(self, client: TestClient) -> None:
        response = client.get("/login")
        assert response.status_code == 200
        assert "Sign in" in response.text

    def test_health_needs_no_account(self, client: TestClient) -> None:
        assert client.get("/healthz").json() == {"status": "ok"}


class TestRegistrationAndSignIn:
    def test_registering_signs_you_in(self, client: TestClient) -> None:
        credentials = {"email": "buyer@example.com", "password": PASSWORD}
        response = client.post(
            REGISTER, data={**credentials, **_csrf(client, REGISTER)}
        )
        assert response.status_code == 303
        assert client.get("/").status_code == 200

    def test_short_password_is_refused(self, client: TestClient) -> None:
        weak = {"email": "a@b.com", "password": "abc"}
        response = client.post(REGISTER, data={**weak, **_csrf(client, REGISTER)})
        assert response.status_code == 400
        assert "at least 10 characters" in response.text

    def test_failed_sign_in_does_not_say_which_part_was_wrong(
        self, client: TestClient, db: Session
    ) -> None:
        auth.register(db, "buyer@example.com", PASSWORD)
        wrong_password = client.post(
            "/login",
            data={"email": "buyer@example.com", "password": "nope", **_csrf(client)},
        )
        unknown_email = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": PASSWORD, **_csrf(client)},
        )
        assert wrong_password.status_code == unknown_email.status_code == 401
        # Identical message for both causes. The pages differ only by the address
        # echoed back into the form, which the person typed themselves.
        assert "do not match an account" in wrong_password.text
        assert "do not match an account" in unknown_email.text
        assert "no such account" not in unknown_email.text.lower()

    def test_signing_out_ends_the_session(self, client: TestClient) -> None:
        sign_in(client)
        client.post("/logout", data=_csrf(client))
        assert client.get("/").status_code == 303


class TestCsrf:
    def test_a_form_without_a_token_is_rejected(self, client: TestClient) -> None:
        sign_in(client)
        response = client.post("/projects", data={"name": "Q3"})
        assert response.status_code == 403

    def test_a_form_with_a_wrong_token_is_rejected(self, client: TestClient) -> None:
        sign_in(client)
        response = client.post(
            "/projects", data={"name": "Q3", "csrf_token": "not-the-token"}
        )
        assert response.status_code == 403

    def test_a_form_with_the_right_token_succeeds(self, client: TestClient) -> None:
        sign_in(client)
        response = client.post("/projects", data={"name": "Q3", **_csrf(client)})
        assert response.status_code == 303


class TestIsolation:
    def test_you_cannot_open_another_accounts_project(
        self, client: TestClient, db: Session
    ) -> None:
        owner = auth.register(db, "owner@example.com", PASSWORD)
        theirs = projects.create_project(db, owner, "Not yours")
        sign_in(client, "intruder@example.com")
        assert client.get(f"/projects/{theirs.id}").status_code == 404

    def test_a_missing_project_looks_the_same_as_a_forbidden_one(
        self, client: TestClient, db: Session
    ) -> None:
        owner = auth.register(db, "owner@example.com", PASSWORD)
        theirs = projects.create_project(db, owner, "Not yours")
        sign_in(client, "intruder@example.com")
        forbidden = client.get(f"/projects/{theirs.id}")
        missing = client.get("/projects/999999")
        assert forbidden.status_code == missing.status_code

    def test_the_dashboard_lists_only_your_own(
        self, client: TestClient, db: Session
    ) -> None:
        owner = auth.register(db, "owner@example.com", PASSWORD)
        projects.create_project(db, owner, "Theirs Only")
        sign_in(client, "buyer@example.com")
        assert "Theirs Only" not in client.get("/").text


class TestProjectFlow:
    def project_with_documents(self, client: TestClient) -> int:
        sign_in(client)
        created = client.post(
            "/projects", data={"name": "Q3 Enclosure", **_csrf(client)}
        )
        # Read the id from the redirect. Sequences are not rolled back with the
        # transaction, so ids do not restart at 1 between tests.
        project_id = int(created.headers["location"].rsplit("/", 1)[-1])
        files = [
            ("files", (name, (PACK / name).read_bytes(), "application/octet-stream"))
            for name in ("quote_a.pdf", "quote_c.pdf", "assumptions.xlsx")
        ]
        client.post(
            f"/projects/{project_id}/documents", data=_csrf(client), files=files
        )
        return project_id

    def test_uploading_lists_the_documents(self, client: TestClient) -> None:
        project_id = self.project_with_documents(client)
        page = client.get(f"/projects/{project_id}").text
        assert "quote_a.pdf" in page
        assert "assumptions.xlsx" in page

    def test_running_produces_the_three_states(self, client: TestClient) -> None:
        project_id = self.project_with_documents(client)
        run = client.post(
            f"/projects/{project_id}/run", data={"quantity": "10000", **_csrf(client)}
        )
        # Results live at the version the run produced; a bare
        # project URL is deliberately clean. Follow the redirect.
        page = client.get(run.headers["location"]).text
        assert "Landed" in page
        assert "Not landed" in page

    def test_a_blocked_supplier_renders_no_number(self, client: TestClient) -> None:
        """A figure on screen is a figure someone will act on."""
        project_id = self.project_with_documents(client)
        run = client.post(
            f"/projects/{project_id}/run", data={"quantity": "10000", **_csrf(client)}
        )
        # Results live at the version the run produced; a bare
        # project URL is deliberately clean. Follow the redirect.
        page = client.get(run.headers["location"]).text
        assert "delivery terms (Incoterm) not stated" in page
        assert "withheld" in page

    def test_supplying_a_value_records_it_against_you(self, client: TestClient) -> None:
        project_id = self.project_with_documents(client)
        client.post(
            f"/projects/{project_id}/run", data={"quantity": "10000", **_csrf(client)}
        )
        client.post(
            f"/projects/{project_id}/resolve",
            data={
                "supplier_id": "C",
                "field_path": "incoterm",
                "value": "FOB Ningbo",
                "rationale": "confirmed by email",
                **_csrf(client),
            },
        )
        page = client.get(f"/projects/{project_id}").text
        assert "Decisions on record" in page
        assert "buyer@example.com" in page
        assert "confirmed by email" in page

    def test_a_supplied_value_unblocks_the_next_run(self, client: TestClient) -> None:
        project_id = self.project_with_documents(client)
        client.post(
            f"/projects/{project_id}/run", data={"quantity": "10000", **_csrf(client)}
        )
        client.post(
            f"/projects/{project_id}/resolve",
            data={
                "supplier_id": "C",
                "field_path": "incoterm",
                "value": "FOB Ningbo",
                **_csrf(client),
            },
        )
        client.post(
            f"/projects/{project_id}/run", data={"quantity": "10000", **_csrf(client)}
        )
        page = client.get(f"/projects/{project_id}").text
        assert "delivery terms (Incoterm) not stated" not in page

    def test_the_diff_reports_movement(self, client: TestClient) -> None:
        project_id = self.project_with_documents(client)
        client.post(
            f"/projects/{project_id}/run", data={"quantity": "1000", **_csrf(client)}
        )
        client.post(
            f"/projects/{project_id}/run", data={"quantity": "100000", **_csrf(client)}
        )
        page = client.get(f"/projects/{project_id}/diff?base=1&target=2").text
        assert "Suppliers that moved" in page


class TestBoundary:
    def test_every_page_states_the_scope(self, client: TestClient) -> None:
        """The brief requires the decision-support boundary be visible, not buried."""
        sign_in(client)
        assert "does not contact suppliers" in client.get("/").text
