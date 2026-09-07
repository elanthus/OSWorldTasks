"""Guard against a dispatch_committed payload leaking the privileged diagnostic.

`pixelgym/grounding/v5/runner.py` used to embed the privileged diagnostic
directly inside the `dispatch_committed` journal event, and a policy's
`post_dispatch_state` argument was built from that same record. Issue #98 split
the diagnostic into its own `privileged_dispatch_diagnostic` event.
`pixelgym/grounding/v5/diagnostics.py` now owns the one sanctioned path for
reading it back (with a legacy fallback for evidence sealed before the split);
no other module under `pixelgym/` or `scripts/` may read `"diagnostic"` out of a
`dispatch_committed` payload. `legacy/grounding/**` had seven such reads; that
tree was deleted under issue #170 (reproducible at git tag
`legacy-grounding-final`), so this scan's `pixelgym/`+`scripts/` scope no
longer has anything excluded.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SANCTIONED_FILES = {
    REPO_ROOT / "pixelgym" / "grounding" / "v5" / "diagnostics.py",
}
DIAGNOSTIC_KEY_READ = re.compile(r'\.get\(\s*"diagnostic"\s*[,)]|\[\s*"diagnostic"\s*\]')


def _python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _function_bodies(path: Path) -> list[str]:
    """Return source text for every function/method, so matches must share a scope."""

    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    bodies = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                bodies.append(segment)
    return bodies


def test_no_pixelgym_or_scripts_function_reads_diagnostic_from_dispatch_committed() -> None:
    offenders = []
    for directory in (REPO_ROOT / "pixelgym", REPO_ROOT / "scripts"):
        for path in _python_files(directory):
            if path in SANCTIONED_FILES:
                continue
            for body in _function_bodies(path):
                if "dispatch_committed" in body and DIAGNOSTIC_KEY_READ.search(body):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
                    break
    assert offenders == []


def test_sanctioned_diagnostic_reader_still_matches_the_allowlisted_pattern() -> None:
    """Keep the allowlist honest: it must name a file that still needs the exception."""

    for path in SANCTIONED_FILES:
        assert path.is_file()
        assert any(
            "dispatch_committed" in body and DIAGNOSTIC_KEY_READ.search(body)
            for body in _function_bodies(path)
        )
