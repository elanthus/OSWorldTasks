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
    assert addendum["applies_from_revision"]
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
