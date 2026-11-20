"""Each statement in the 2026-11-11 guest check's dated erratum holds against the stored files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "artifacts/osworld-guest-check-2026-11-11"
ERRATUM = EVIDENCE / "ERRATUM-2026-11-13.md"


def _json(name: str) -> Any:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def test_erratum_exists_beside_the_unchanged_results() -> None:
    assert ERRATUM.is_file()
    assert "Erratum, 2026-11-13 (UTC)" in ERRATUM.read_text(encoding="utf-8")


def test_run_started_on_a_later_utc_date_than_the_folder_name() -> None:
    started = _json("provenance.json")["run_started_utc"]
    assert started == "2026-11-12T00:54:49Z"
    assert not started.startswith(EVIDENCE.name.removeprefix("osworld-guest-check-"))


def test_recorded_contact_sheet_path_dangles_and_the_sheet_is_at_the_folder_root() -> None:
    recorded = _json("real-golden-episode.json")["contact_sheet_path"]
    assert recorded == "artifacts/osworld-guest-check-2026-11-11/real-golden/contact-sheet.png"
    assert not (ROOT / recorded).exists()
    assert (EVIDENCE / "contact-sheet.png").is_file()
    listed = {f["path"] for f in _json("images.manifest.json")["files"]}
    assert "contact-sheet.png" in listed


def test_step_111_records_no_submission_rather_than_correct_fields() -> None:
    episode = _json("real-golden-episode.json")
    (subset,) = [
        row
        for row in episode["real_reward_timing_subset"]
        if row["name"] == "correct-fields-without-submit"
    ]
    assert subset["step"] == 111 and subset["reward"] == 0.0
    assert "fields" not in json.dumps(sorted(subset))
    (step,) = [row for row in episode["trace"] if row["step"] == 111]
    assert step["evaluator"]["submitted"] is False
    assert len(step["evaluator"]["mismatched_fields"]) == 7
