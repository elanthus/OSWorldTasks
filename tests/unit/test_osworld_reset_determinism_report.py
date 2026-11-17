"""Fast tests for the guest reset-determinism report generator (stored files only; no guest)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from scripts import generate_osworld_reset_determinism_report as report
from scripts.run_chromium_reset_determinism import frame_sha256

ROOT = Path(__file__).resolve().parents[2]


def _frame(value: int) -> np.ndarray:
    return np.full((4, 6, 3), value, dtype=np.uint8)


def _run(folder: Path, *, close_returned: bool = True, skipped: list[dict] | None = None) -> None:
    """A two-reset run of seed 5 whose second reset differs, with both PNGs saved."""
    a, b = _frame(10), _frame(200)
    folder.mkdir(parents=True)
    Image.fromarray(a).save(folder / "seed-5-reset-0.png")
    Image.fromarray(b).save(folder / "seed-5-reset-1.png")
    resets = [
        {
            "reset_index": i,
            "frame_sha256": frame_sha256(f),
            "task_sha256": "t" * 64,
            "form_state_sha256": "s" * 64,
            "seconds": 200.0 + i,
            "acknowledged_after_reset": 0,
        }
        for i, f in enumerate((a, b))
    ]
    evidence: dict[str, Any] = {
        "schema_version": report.SCHEMA_VERSION,
        "provenance": {
            "run_started_utc": "2026-11-16T00:00:00Z",
            "git_revision": "0" * 40,
            "worktree_clean_at_start": True,
            "guest_bundle_sha256_by_seed": {"5": "b" * 64},
            "input_contract": "pixelgym-vendor-form-input-v3",
            "setup_acknowledged_inputs": 0,
        },
        "seeds": [5],
        "resets_per_seed": 3,
        "budget_minutes": 85.0,
        "per_seed": [
            {
                "seed": 5,
                "resets": resets,
                "distinct_frame_sha256": sorted({r["frame_sha256"] for r in resets}),
                "distinct_task_sha256": ["t" * 64],
                "distinct_form_state_sha256": ["s" * 64],
                "differing_frame_pairs": [
                    {
                        "resets": [0, 1],
                        "comparable": True,
                        "differing_pixel_count": 24,
                        "max_per_channel_delta": 190,
                        "differing_pixel_bbox_xyxy": [0, 0, 5, 3],
                    }
                ],
                "pair_count": 1,
                "reset_errors": [],
            }
        ],
        "skipped": skipped or [],
        "integration_metadata": {"provider": "docker-local", "upstream_tag": "v2026.06.24"},
        "provider_close": {
            "attempted": True,
            "returned": close_returned,
            "error": None if close_returned else "Traceback ...",
            "suppressed_close_errors": [],
        },
        "provider_closed": close_returned,
        "elapsed_seconds": 401.0,
    }
    (folder / "results.json").write_text(json.dumps(evidence), encoding="utf-8")


def _render(tmp_path: Path, folder: Path) -> str:
    out = tmp_path / "report.md"
    assert report.main([str(folder), "--output", str(out), "--repository-root", str(tmp_path)]) == 0
    return out.read_text(encoding="utf-8")


def test_counts_hash_groups_and_raw_difference_come_from_the_file(tmp_path: Path) -> None:
    _run(tmp_path / "run")
    text = _render(tmp_path, tmp_path / "run")
    assert "| 5 | `bbbbbbbbbbbb` | 2 | 2 | 1 | 1 | 1/1 | 24 | 190 | [0, 0, 5, 3] | 0 |" in text
    assert "2 completed, 0 failed, 0 skipped" in text
    assert "Seeds with one frame hash across all completed resets: 0 of 1." in text
    assert "Seeds with one task hash and one application-state hash: 1 of 1." in text
    assert "| `upstream_tag` | `v2026.06.24` |" in text


def test_a_png_that_does_not_match_its_recorded_hash_is_reported(tmp_path: Path) -> None:
    _run(tmp_path / "run")
    Image.fromarray(_frame(11)).save(tmp_path / "run" / "seed-5-reset-1.png")
    text = _render(tmp_path, tmp_path / "run")
    assert "| `seed-5-reset-0.png` | yes |" in text
    assert "| `seed-5-reset-1.png` | no |" in text


def test_skips_and_a_failed_close_are_reported(tmp_path: Path) -> None:
    skipped = [{"seed": 5, "reset_index": 2, "reason": "time budget"}]
    _run(tmp_path / "run", close_returned=False, skipped=skipped)
    text = _render(tmp_path, tmp_path / "run")
    assert "1 skipped by the 85-minute budget" in text
    assert "- seed 5 reset 2: time budget" in text
    assert "returned no, error recorded" in text
    assert "Runs whose provider close returned without error: 0 of 1." in text


def test_wrong_schema_is_rejected(tmp_path: Path) -> None:
    _run(tmp_path / "run")
    path = tmp_path / "run" / "results.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["schema_version"] = "something-else"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected schema"):
        report.load(tmp_path / "run")


def test_hash_groups_keep_first_seen_order() -> None:
    summary = {
        "resets": [
            {"reset_index": 0, "frame_sha256": "x"},
            {"reset_index": 1, "frame_sha256": "y"},
            {"reset_index": 2, "frame_sha256": "x"},
        ]
    }
    assert report.hash_groups(summary) == [("x", [0, 2]), ("y", [1])]


def test_committed_report_matches_regeneration(tmp_path: Path) -> None:
    runs = sorted(
        p.name for p in (ROOT / "artifacts").glob("osworld-reset-determinism-2026-11-15-?")
    )
    committed = ROOT / "artifacts" / "osworld-reset-determinism-2026-11-15-report.md"
    if not committed.is_file():
        pytest.skip("guest report not generated yet")
    out = tmp_path / "report.md"
    args = [*(f"artifacts/{r}" for r in runs), "--output", str(out)]
    assert report.main(args) == 0
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")


def test_a_seed_whose_every_reset_failed_still_renders(tmp_path: Path) -> None:
    _run(tmp_path / "run")
    path = tmp_path / "run" / "results.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["seeds"] = [5, 9]
    evidence["provenance"]["guest_bundle_sha256_by_seed"]["9"] = "c" * 64
    errors = [{"reset_index": i, "error": "Traceback ...", "seconds": 1.0} for i in range(3)]
    evidence["per_seed"].append({"seed": 9, "reset_errors": errors})
    path.write_text(json.dumps(evidence), encoding="utf-8")
    text = _render(tmp_path, tmp_path / "run")
    assert "2 completed, 3 failed, 0 skipped" in text
    assert "| 9 | `cccccccccccc` | 0 | 0 | 0 | 0 | 0/0 | 0 | 0 | — | 3 |" in text


def test_skips_for_a_cleanup_failure_are_not_attributed_to_the_budget(tmp_path: Path) -> None:
    reason = "provider cleanup failed after a reset failure"
    skipped = [{"seed": 5, "reset_index": 2, "reason": reason}]
    _run(tmp_path / "run", skipped=skipped)
    text = _render(tmp_path, tmp_path / "run")
    assert "0 skipped by the 85-minute budget, 1 skipped for another reason (listed below)" in text
    assert f"- seed 5 reset 2: {reason}" in text


def _set_metadata(folder: Path, **fields: Any) -> None:
    path = folder / "results.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["integration_metadata"].update(fields)
    path.write_text(json.dumps(evidence), encoding="utf-8")


def test_scope_claims_come_from_the_recorded_runs(tmp_path: Path) -> None:
    _run(tmp_path / "a")
    _run(tmp_path / "b")
    _set_metadata(tmp_path / "a", guest_artifact_sha256="sha256:one")
    _set_metadata(tmp_path / "b", guest_artifact_sha256="sha256:one")
    out = tmp_path / "report.md"
    args = ["a", "b", "--output", str(out), "--repository-root", str(tmp_path)]
    assert report.main(args) == 0
    text = out.read_text(encoding="utf-8")
    assert "No reset failed, so each run used one provider." in text
    assert "One guest image (`guest_artifact_sha256` `sha256:one` in every run)" in text
    assert "do not record the host" in text
    _set_metadata(tmp_path / "b", guest_artifact_sha256="sha256:two")
    path = tmp_path / "b" / "results.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["per_seed"][0]["reset_errors"] = [{"reset_index": 2, "error": "x", "seconds": 1.0}]
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert report.main(args) == 0
    text = out.read_text(encoding="utf-8")
    assert "2 distinct guest artifact digests across runs" in text
    assert "may have used more than one provider" in text
    assert "One guest image" not in text


def test_a_run_folder_outside_the_repository_root_loads(tmp_path: Path) -> None:
    _run(tmp_path / "elsewhere" / "run")
    (tmp_path / "repo").mkdir()
    out = tmp_path / "report.md"
    folder = str(tmp_path / "elsewhere" / "run")
    args = [folder, "--output", str(out), "--repository-root", str(tmp_path / "repo")]
    assert report.main(args) == 0
    assert "2 completed, 0 failed" in out.read_text(encoding="utf-8")
