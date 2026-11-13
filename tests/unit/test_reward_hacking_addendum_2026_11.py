"""The November 2026 reward-hacking addendum classifies the barrier surfaces with real evidence.

Every disposition must come from the frozen audit's vocabulary, every entry must state its
surface, mitigation, evidence, and residual risk, and every ``tests/...py (name, ...)`` citation
must name a test file and test functions that exist, so the classification cannot point at
evidence that is not there.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDENDUM = REPO_ROOT / "artifacts" / "reward-hacking-addendum-2026-11.json"
DISPOSITIONS = {"blocked", "tested", "mitigated", "known limitation"}
_CITATION = re.compile(r"(tests/[\w/]+\.py)(?: \(([^)]*)\))?")


def _load() -> dict:
    return json.loads(ADDENDUM.read_text(encoding="utf-8"))


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def test_entries_use_the_audit_vocabulary_and_state_every_part() -> None:
    addendum = _load()

    assert addendum["kind"] == "reward_hacking_addendum"
    assert (REPO_ROOT / addendum["supplements"]).is_file()
    attacks = addendum["attacks"]
    assert {entry["disposition"] for entry in attacks} <= DISPOSITIONS
    for entry in attacks:
        for key in ("attack", "surface", "mitigation", "evidence", "residual_risk"):
            assert entry[key].strip(), (entry["attack"], key)


def test_every_cited_test_file_and_function_exists() -> None:
    cited = 0
    for entry in _load()["attacks"]:
        for path_text, names in _CITATION.findall(entry["evidence"]):
            path = REPO_ROOT / path_text
            assert path.is_file(), path_text
            available = _functions(path) if path.name.startswith("test_") else set()
            for name in filter(None, (part.strip() for part in names.split(","))):
                if name.startswith("test_"):
                    assert name in available, f"{path_text} has no {name}"
                    cited += 1
    assert cited >= 10


def test_the_new_endpoints_are_each_classified() -> None:
    text = json.dumps(_load())

    for surface in ("POST /api/input-ack", "GET /api/input-ack", "/api/page-ready", "page token"):
        assert surface in text, surface


def test_every_cited_artifact_exists() -> None:
    # The pointer-park entry cites the 248097d guest run; a stale or moved path would let the
    # residual risk point at evidence that is not there.
    cited = re.findall(r"artifacts/[\w./-]+\.json", json.dumps(_load()))
    assert any("osworld-guest-check-2026-11-12-248097d" in path for path in cited)
    for path in cited:
        assert (REPO_ROOT / path).is_file(), path
