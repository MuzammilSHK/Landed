"""The domain core stays independent of infrastructure.

`landed.core` must not import from `db`, `web`, or `services`. That constraint is
what lets the evaluation harness score the engine headlessly, lets the web layer be
replaced without touching domain logic, and keeps arithmetic out of route handlers.

It is also load-bearing for the project's central claim: `cost_engine` cannot reach
a model client, so landed-cost arithmetic is deterministic by construction rather
than by discipline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "landed" / "core"
FORBIDDEN_FOR_CORE = ("landed.db", "landed.web", "landed.services", "landed.eval")
MODEL_CLIENTS = ("anthropic", "openai")


def _imported_modules(path: Path) -> set[str]:
    """Every module name reached by an import statement in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _core_modules() -> list[Path]:
    return sorted(p for p in CORE.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: p.name)
def test_core_does_not_import_infrastructure(module: Path) -> None:
    leaks = {
        name
        for name in _imported_modules(module)
        for forbidden in FORBIDDEN_FOR_CORE
        if name == forbidden or name.startswith(f"{forbidden}.")
    }
    assert not leaks, f"{module.name} imports infrastructure: {sorted(leaks)}"


def test_cost_engine_cannot_reach_a_model_client() -> None:
    """Arithmetic never passes through an LLM. Enforced, not merely intended."""
    imported = _imported_modules(CORE / "cost_engine.py")
    leaks = {name for name in imported if name.split(".")[0] in MODEL_CLIENTS}
    assert not leaks, f"cost_engine imports a model client: {sorted(leaks)}"
