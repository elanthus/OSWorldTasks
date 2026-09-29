"""Amendment files must link back to the frozen file they supplement and be reachable."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"

# Amendment file -> frozen file it supplements (relative to artifacts/).
AMENDMENTS = {
    "grounding-protocol-amendment-2026-08-14-note.md": "grounding-protocol.md",
}


def test_amendment_links_back_to_supplemented_file() -> None:
    for amendment, target in AMENDMENTS.items():
        text = (ARTIFACTS / amendment).read_text(encoding="utf-8")
        assert (ARTIFACTS / target).is_file()
        assert f"]({target})" in text, f"{amendment} must link to {target}"


def test_amendment_is_linked_from_evidence_index() -> None:
    index = (ROOT / "docs/evidence-index.md").read_text(encoding="utf-8")
    for amendment in AMENDMENTS:
        assert f"](../artifacts/{amendment})" in index, f"{amendment} is not indexed"


def test_amendment_files_are_all_registered() -> None:
    found = {path.name for path in ARTIFACTS.glob("*-amendment-*.md")}
    assert found == set(AMENDMENTS)
