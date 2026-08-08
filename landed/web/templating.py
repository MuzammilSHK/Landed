"""Shared Jinja environment.

Formatting helpers live here rather than in templates so money is rendered one way
everywhere. A total shown to two decimals in one place and three in another reads as
a discrepancy even when the numbers agree.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

from landed.core.schema import QuoteState

TEMPLATE_DIR = Path(__file__).parent / "templates"

STATE_LABEL = {
    QuoteState.LANDED.value: "Landed",
    QuoteState.CONTESTED.value: "Contested",
    QuoteState.NOT_LANDED.value: "Not landed",
}

STATE_HINT = {
    QuoteState.LANDED.value: "Complete and sourced",
    QuoteState.CONTESTED.value: "Sources disagree",
    QuoteState.NOT_LANDED.value: "Required detail missing",
}


def money(value: Decimal | str | None, places: int = 2) -> str:
    """Thousands-separated, fixed precision. Blank for absent, never zero.

    A missing total displayed as 0.00 reads as free.
    """
    if value in (None, ""):
        return "—"
    return f"{Decimal(str(value)):,.{places}f}"


def percent(value: Decimal | str | None) -> str:
    if value in (None, ""):
        return "—"
    number = Decimal(str(value))
    return f"{number:+.1f}%"


def signed(value: Decimal | str | None) -> str:
    if value in (None, ""):
        return "—"
    return f"{Decimal(str(value)):+,.2f}"


def cite(source: dict | None) -> str:
    """Render a citation compactly: file, page, and cell where they exist."""
    if not source:
        return ""
    parts = [source.get("file", "")]
    if source.get("page"):
        parts.append(f"p.{source['page']}")
    if source.get("cell"):
        sheet = f"{source['sheet']}!" if source.get("sheet") else ""
        parts.append(f"{sheet}{source['cell']}")
    return " ".join(part for part in parts if part)


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["money"] = money
templates.env.filters["percent"] = percent
templates.env.filters["signed"] = signed
templates.env.filters["cite"] = cite
templates.env.globals["STATE_LABEL"] = STATE_LABEL
templates.env.globals["STATE_HINT"] = STATE_HINT
