"""CLI rendering tests.

The terminal output is a demo surface, so what it shows and what it withholds both
matter. A supplier we declined to cost must never display a number, and the reason it
was declined must be on screen next to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from landed import cli
from tests.test_pipeline import PackStub

PACK = Path(__file__).resolve().parents[1] / "packs" / "synthetic"
Capture = pytest.CaptureFixture[str]


@pytest.fixture(autouse=True)
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_provider", lambda _name=None: PackStub())


def run(*args: str) -> int:
    return cli.main(["compare", "--pack", str(PACK), *args])


class TestRendering:
    def test_exits_cleanly(self, capsys: Capture) -> None:
        assert run("--quantity", "10000") == 0
        assert "LANDED COST COMPARISON" in capsys.readouterr().out

    def test_every_supplier_appears_with_a_state(self, capsys: Capture) -> None:
        run("--quantity", "10000")
        out = capsys.readouterr().out
        for marker in ("[LANDED]", "[CONTESTED]", "[NOT LANDED]"):
            assert marker in out

    def test_blocked_suppliers_show_no_number(self, capsys: Capture) -> None:
        """A dash, not a total. A number on screen is a number someone will act on."""
        run("--quantity", "10000")
        for line in capsys.readouterr().out.splitlines():
            if line.startswith(("[CONTESTED]", "[NOT LANDED]")):
                assert "/unit" not in line

    def test_reasons_are_printed_for_blocked_suppliers(self, capsys: Capture) -> None:
        run("--quantity", "10000")
        out = capsys.readouterr().out
        assert "WHY THESE CANNOT BE COMPARED YET" in out
        assert "delivery terms (Incoterm) not stated" in out
        assert "minimum order quantity disagrees" in out

    def test_conflict_sources_are_cited(self, capsys: Capture) -> None:
        run("--quantity", "10000")
        out = capsys.readouterr().out
        assert "quote_b.pdf" in out
        assert "profile_b.docx" in out

    def test_advisories_are_separated_from_blockers(self, capsys: Capture) -> None:
        run("--quantity", "10000")
        out = capsys.readouterr().out
        assert "WORTH A SECOND LOOK" in out
        assert "not acted on" in out          # the injection attempt
        assert "scanned image" in out          # the vision read

    def test_winner_shows_an_itemized_breakdown(self, capsys: Capture) -> None:
        run("--quantity", "10000")
        out = capsys.readouterr().out
        assert "LOWEST LANDED COST" in out
        for term in ("goods", "tooling (amortized)", "freight", "duty", "PER UNIT"):
            assert term in out


class TestJsonOutput:
    def test_json_is_machine_readable(self, capsys: Capture) -> None:
        run("--quantity", "10000", "--json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["quantity"] == 10_000
        assert len(payload["suppliers"]) == 5

    def test_json_carries_states_and_refusals(self, capsys: Capture) -> None:
        run("--quantity", "10000", "--json")
        payload = json.loads(capsys.readouterr().out)
        blocked = [s for s in payload["suppliers"] if s["result"].get("reason")]
        assert blocked


class TestArguments:
    def test_missing_pack_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["compare", "--pack", "does/not/exist"])

    def test_quantity_changes_the_result(self, capsys: Capture) -> None:
        """Tooling amortization means volume moves the per-unit cost."""
        run("--quantity", "1000")
        small = capsys.readouterr().out
        run("--quantity", "200000")
        large = capsys.readouterr().out
        assert small != large
