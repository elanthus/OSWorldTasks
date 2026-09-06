from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULE_PATHS = (
    *sorted((REPOSITORY_ROOT / "pixelgym/backends").rglob("*.py")),
    REPOSITORY_ROOT / "pixelgym/env.py",
    REPOSITORY_ROOT / "pixelgym/evaluator.py",
    REPOSITORY_ROOT / "pixelgym/actions.py",
    REPOSITORY_ROOT / "pixelgym/task_spec.py",
    REPOSITORY_ROOT / "pixelgym/serialization.py",
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPOSITORY_ROOT).with_suffix("").parts)


def _imported_modules_from_source(path: Path, source: str) -> list[str]:
    imported: list[str] = []
    module_name = _module_name(path)
    package_name = (
        module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    )
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_from = node.module or ""
            if node.level:
                imported_from = importlib.util.resolve_name(
                    "." * node.level + imported_from, package_name
                )
            imported.extend(f"{imported_from}.{alias.name}" for alias in node.names)
    return imported


def _imported_modules(path: Path) -> list[str]:
    return _imported_modules_from_source(path, path.read_text(encoding="utf-8"))


def test_relative_imports_are_resolved_before_boundary_matching() -> None:
    backend_module = REPOSITORY_ROOT / "pixelgym/backends/fake.py"

    assert _imported_modules_from_source(
        backend_module, "from .. import grounding\n"
    ) == ["pixelgym.grounding"]


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
