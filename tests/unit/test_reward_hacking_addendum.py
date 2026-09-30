"""The September 2026 reward-hacking addendum parses and uses the frozen audit vocabulary."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDENDUM = REPO_ROOT / "artifacts" / "reward-hacking-addendum-2026-09.json"
FROZEN_AUDIT = (
    REPO_ROOT / "artifacts" / "day-2-rev-2026-09-06-issues-95-101" / "raw" / "reward-hacking.json"
)
DISPOSITIONS = {"blocked", "tested", "mitigated", "known limitation"}


def test_addendum_parses_and_records_the_task_id_precompute_surface() -> None:
    addendum = json.loads(ADDENDUM.read_text(encoding="utf-8"))

    assert addendum["decision_date"] == "2026-09-30"
    assert "applies_from_revision" not in addendum
    assert addendum["applies_from"] == {
        "pull_request": "elanthus/OSWorldTasks#230",
        "branch": "cleanup/wp5-opaque-episode-id",
    }
    assert addendum["applies_from_note"].strip()
    (entry,) = addendum["attacks"]
    assert entry["attack"] == "Precompute task_id to answers from the public generator"
    assert entry["disposition"] == "mitigated"
    for key in ("previous_exposure", "mitigation", "evidence"):
        assert entry[key].strip()


def test_addendum_disposition_vocabulary_matches_the_frozen_audit() -> None:
    addendum = json.loads(ADDENDUM.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN_AUDIT.read_text(encoding="utf-8"))

    frozen_dispositions = {item["disposition"] for item in frozen["attacks"]}
    addendum_dispositions = {item["disposition"] for item in addendum["attacks"]}
    assert frozen_dispositions <= DISPOSITIONS
    assert addendum_dispositions <= frozen_dispositions


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for key, value in node.items() for text in [key, *_strings(value)]]
    if isinstance(node, list):
        return [text for value in node for text in _strings(value)]
    return []


def test_addendum_contains_no_placeholder_strings() -> None:
    addendum = json.loads(ADDENDUM.read_text(encoding="utf-8"))

    assert [text for text in _strings(addendum) if "<" in text] == []
