"""Invariant 14: build-time capture instrumentation must stay unreachable from evaluation.

The v3/v4-family variants of this check (against the now-deleted `legacy/grounding/`
evaluation modules) were removed with issue #170; this test covers the shipped
`pixelgym.grounding.evaluation` adapter that remains in the wheel.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
FORBIDDEN_MODULE = "pixelgym.grounding.capture"


def test_evaluation_adapter_does_not_import_capture_instrumentation() -> None:
    source = REPOSITORY_ROOT / "pixelgym/grounding/evaluation.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    from_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert FORBIDDEN_MODULE not in from_modules
    assert FORBIDDEN_MODULE not in imported_modules
