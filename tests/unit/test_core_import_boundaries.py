from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULE_PATHS = (
    *sorted((REPOSITORY_ROOT / "pixelgym/backends").rglob("*.py")),
    REPOSITORY_ROOT / "pixelgym/env.py",
    REPOSITORY_ROOT / "pixelgym/evaluator.py",
    REPOSITORY_ROOT / "pixelgym/actions.py",
    REPOSITORY_ROOT / "pixelgym/task_spec.py",
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPOSITORY_ROOT).with_suffix("").parts)


def _imported_modules(path: Path) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def test_core_modules_do_not_import_grounding() -> None:
    forbidden_edges = [
        f"{_module_name(path)} -> {imported}"
        for path in CORE_MODULE_PATHS
        for imported in _imported_modules(path)
        if imported == "pixelgym.grounding" or imported.startswith("pixelgym.grounding.")
    ]

    assert not forbidden_edges, (
        "forbidden core-to-grounding import edge(s):\n" + "\n".join(forbidden_edges)
    )
