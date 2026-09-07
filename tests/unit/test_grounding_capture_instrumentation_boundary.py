"""Invariant 14: build-time capture instrumentation must stay unreachable from evaluation.

The v3/v4-family variants of this check (against the now-deleted `legacy/grounding/`
evaluation modules) were removed with issue #170; this test covers every shipped
evaluation adapter that remains in the wheel.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
FORBIDDEN_MODULE = "pixelgym.grounding.capture"

EVALUATION_ADAPTERS = (
    "pixelgym/grounding/evaluation.py",
    "pixelgym/grounding/pilot.py",
    "pixelgym/grounding/v5/runner.py",
    "pixelgym/grounding/v5/calibration_runner.py",
)


@pytest.mark.parametrize("relative_path", EVALUATION_ADAPTERS)
def test_evaluation_adapter_does_not_import_capture_instrumentation(
    relative_path: str,
) -> None:
    source = REPOSITORY_ROOT / relative_path
    tree = ast.parse(source.read_text(encoding="utf-8"))

    from_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    from_module_names = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert FORBIDDEN_MODULE not in from_modules
    assert FORBIDDEN_MODULE not in imported_modules
    assert ("pixelgym.grounding", "capture") not in from_module_names
