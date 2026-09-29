"""The million-episode design note is generated into docs, not the retired plans directory."""

from __future__ import annotations

import json
from pathlib import Path

from flows import generate_seed_policy_evidence as generator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = REPOSITORY_ROOT / "artifacts/platform/seed-policy-fanout-evidence-v1.json"


def test_design_note_target_is_the_docs_path() -> None:
    assert generator.DESIGN_NOTE_PATH == Path("docs/million-episode-design-note.md")
    assert generator.DESIGN_NOTE_PATH.parts[0] != "plans"


def test_checked_in_note_matches_generator_output_from_stored_evidence(tmp_path: Path) -> None:
    evidence = json.loads(EVIDENCE.read_text())
    rendered = tmp_path / "note.md"
    generator._write_note(rendered, evidence)
    assert rendered.read_bytes() == (REPOSITORY_ROOT / generator.DESIGN_NOTE_PATH).read_bytes()


def test_retired_plans_note_path_is_absent() -> None:
    assert not (REPOSITORY_ROOT / "plans" / "million-episode-grounding-evaluation.md").exists()
