"""Each statement in the dated erratum for #280's 2026-11-12 guest check holds against its files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "artifacts/osworld-guest-check-2026-11-12"
ERRATUM = EVIDENCE / "ERRATUM-2026-11-13.md"


def _json(name: str) -> Any:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def test_erratum_exists_beside_the_unchanged_attempts() -> None:
    assert "Erratum, 2026-11-13 (UTC)" in ERRATUM.read_text(encoding="utf-8")
    assert "Step 111 had every field correct" in (EVIDENCE / "README.md").read_text("utf-8")


def test_step_111_records_no_submission_rather_than_correct_fields() -> None:
    episode = _json("attempt-3/real-golden-episode.json")
    (subset,) = [
        row
        for row in episode["real_reward_timing_subset"]
        if row["name"] == "correct-fields-without-submit"
    ]
    assert subset["step"] == 111 and subset["reward"] == 0.0
    assert set(subset) == {"name", "passed", "reward", "step", "terminated", "truncated"}
    (step,) = [row for row in episode["trace"] if row["step"] == 111]
    assert step["evaluator"]["submitted"] is False
    assert len(step["evaluator"]["mismatched_fields"]) == 7


def test_recorded_contact_sheet_path_dangles_and_the_sheet_is_in_the_attempt_folder() -> None:
    recorded = _json("attempt-3/real-golden-episode.json")["contact_sheet_path"]
    assert (
        recorded
        == "artifacts/osworld-guest-check-2026-11-12/attempt-3/real-golden/contact-sheet.png"
    )
    assert not (ROOT / recorded).exists()
    assert (EVIDENCE / "attempt-3/contact-sheet.png").is_file()
    listed = {f["path"] for f in _json("attempt-3/images.manifest.json")["files"]}
    assert "contact-sheet.png" in listed
