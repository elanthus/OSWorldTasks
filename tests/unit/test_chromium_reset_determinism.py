"""Fast tests for the Chromium reset-determinism recorder's pure parts and its report generator.

No browser is launched: the recorder's comparison functions and the generator run on small
synthetic frames and evidence files built here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from scripts import generate_chromium_reset_determinism_report as report
from scripts import run_chromium_reset_determinism as recorder


def _frame(value: int = 10) -> np.ndarray:
    return np.full((4, 6, 3), value, dtype=np.uint8)


def test_raw_difference_counts_pixels_and_max_delta_before_tolerance() -> None:
    a, b = _frame(), _frame()
    b[1, 2] = (10, 13, 10)
    b[3, 5] = (11, 10, 10)
    diff = recorder.raw_difference(a, b)
    assert diff == {
        "comparable": True,
        "differing_pixel_count": 2,
        "max_per_channel_delta": 3,
        "differing_pixel_bbox_xyxy": [2, 1, 6, 4],
    }


def test_raw_difference_of_identical_frames_is_zero() -> None:
    diff = recorder.raw_difference(_frame(), _frame())
    assert diff["differing_pixel_count"] == 0
    assert diff["differing_pixel_bbox_xyxy"] is None


def test_raw_difference_reports_incomparable_shapes() -> None:
    diff = recorder.raw_difference(_frame(), np.zeros((4, 5, 3), dtype=np.uint8))
    assert diff["comparable"] is False


def test_frame_sha256_is_byte_identity() -> None:
    a, b = _frame(), _frame()
    assert recorder.frame_sha256(a) == recorder.frame_sha256(b)
    b[0, 0, 0] += 1
    assert recorder.frame_sha256(a) != recorder.frame_sha256(b)


def _reset(index: int, frame: np.ndarray, task: str = "t", form: str = "f") -> dict[str, Any]:
    return {
        "reset_index": index,
        "frame_sha256": recorder.frame_sha256(frame),
        "task_sha256": task,
        "form_state_sha256": form,
    }


def test_summarize_seed_records_every_differing_pair_only() -> None:
    frames = [_frame(), _frame(), _frame()]
    frames[2][0, 0] = 0
    resets = [_reset(i, f) for i, f in enumerate(frames)]
    summary = recorder.summarize_seed(7, resets, frames)
    assert summary["pair_count"] == 3
    assert [d["resets"] for d in summary["differing_frame_pairs"]] == [[0, 2], [1, 2]]
    assert all(d["differing_pixel_count"] == 1 for d in summary["differing_frame_pairs"])
    assert len(summary["distinct_frame_sha256"]) == 2


def test_summarize_seed_keeps_semantic_and_visual_apart() -> None:
    frames = [_frame(), _frame()]
    resets = [_reset(0, frames[0], task="a"), _reset(1, frames[1], task="b")]
    summary = recorder.summarize_seed(7, resets, frames)
    assert summary["differing_frame_pairs"] == []
    assert len(summary["distinct_task_sha256"]) == 2


def test_cli_requires_eight_seeds_and_five_resets(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        recorder.main(["--output", str(tmp_path / "x.json"), "--seeds", "1", "2"])
    with pytest.raises(SystemExit):
        recorder.main(["--output", str(tmp_path / "x.json"), "--resets", "4"])


def _evidence(
    root: Path, name: str, system: str, frames: dict[int, np.ndarray], *, save: bool = True
) -> Path:
    frames_dir = f"frames-{name}"
    per_seed = []
    for seed, frame in frames.items():
        resets = [_reset(i, frame) for i in range(5)]
        per_seed.append(recorder.summarize_seed(seed, resets, [frame] * 5))
        if save:
            (root / frames_dir).mkdir(exist_ok=True)
            Image.fromarray(frame).save(root / frames_dir / f"seed-{seed}-reset-0.png")
    evidence = {
        "schema_version": recorder.SCHEMA_VERSION,
        "provenance": {
            "recorded_at_utc": "2026-11-15T00:00:00Z",
            "git_revision": "abc",
            "worktree_clean": True,
            "platform": {"system": system, "release": "1", "machine": "x", "python": "3.12"},
            "playwright": "1.62.0",
            "chromium_version": "140",
            "renderer_contract": {"identity": "sha256:r"},
            "viewport": [6, 4],
            "isolation": "fresh context",
        },
        "seeds": sorted(frames),
        "resets_per_seed": 5,
        "frames_dir": frames_dir if save else None,
        "per_seed": per_seed,
        "total_seconds": 1.0,
    }
    path = root / f"{name}.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def test_report_compares_hosts_and_reports_raw_pixel_differences(tmp_path: Path) -> None:
    other = _frame()
    other[0, 0] = (10, 10, 17)
    _evidence(tmp_path, "mac", "Darwin", {7: _frame(), 11: _frame(20)})
    _evidence(tmp_path, "linux", "Linux", {7: other, 11: _frame(20)})
    out = tmp_path / "REPORT.md"
    assert (
        report.main(
            ["mac.json", "linux.json", "--output", str(out), "--repository-root", str(tmp_path)]
        )
        == 0
    )
    text = out.read_text(encoding="utf-8")
    assert "Frame hash sets equal on 1 of 2 common seeds" in text
    assert "| 7 | no | yes | yes | 1 | 7 | [0, 0, 1, 1] |" in text
    assert "| 11 | yes | yes | yes | 0 | 0 | None |" in text
    assert "Perceptual visual stability" in text and "Not computed" in text


def test_report_without_pngs_says_so(tmp_path: Path) -> None:
    _evidence(tmp_path, "mac", "Darwin", {7: _frame()}, save=False)
    _evidence(tmp_path, "linux", "Linux", {7: _frame()}, save=False)
    out = tmp_path / "REPORT.md"
    report.main(
        ["mac.json", "linux.json", "--output", str(out), "--repository-root", str(tmp_path)]
    )
    assert "| 7 | yes | yes | yes | no PNG | no PNG | no PNG |" in out.read_text(encoding="utf-8")


def test_report_rejects_other_schemas(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text(json.dumps({"schema_version": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected schema"):
        report.load(tmp_path / "bad.json")


def test_report_finds_frames_recorded_under_another_workspace(tmp_path: Path) -> None:
    other = _frame()
    other[2, 3] = (10, 12, 10)
    _evidence(tmp_path, "mac", "Darwin", {7: _frame()})
    linux = _evidence(tmp_path, "linux", "Linux", {7: other})
    record = json.loads(linux.read_text(encoding="utf-8"))
    record["frames_dir"] = "ci-workspace/frames-linux"
    linux.write_text(json.dumps(record), encoding="utf-8")
    out = tmp_path / "REPORT.md"
    report.main(
        ["mac.json", "linux.json", "--output", str(out), "--repository-root", str(tmp_path)]
    )
    assert "| 7 | no | yes | yes | 1 | 2 | [3, 2, 4, 3] |" in out.read_text(encoding="utf-8")


def test_cli_rejects_repeated_seeds(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        recorder.main(["--output", str(tmp_path / "x.json"), "--seeds", *(["7"] * 8)])
    assert exit_info.value.code == 2
    assert not (tmp_path / "x.json").exists()


def test_report_prefers_the_recorded_frames_directory(tmp_path: Path) -> None:
    # Recorded with --output evidence/linux.json --frames-dir images/linux: the PNGs are not
    # beside the evidence file.
    other = _frame()
    other[1, 1] = (10, 15, 10)
    _evidence(tmp_path, "mac", "Darwin", {7: _frame()})
    linux = _evidence(tmp_path, "linux", "Linux", {7: other})
    (tmp_path / "evidence").mkdir()
    (tmp_path / "images").mkdir()
    (tmp_path / "frames-linux").rename(tmp_path / "images" / "linux")
    record = json.loads(linux.read_text(encoding="utf-8"))
    record["frames_dir"] = "images/linux"
    (tmp_path / "evidence" / "linux.json").write_text(json.dumps(record), encoding="utf-8")
    linux.unlink()
    out = tmp_path / "REPORT.md"
    report.main(
        [
            "mac.json",
            "evidence/linux.json",
            "--output",
            str(out),
            "--repository-root",
            str(tmp_path),
        ]
    )
    assert "| 7 | no | yes | yes | 1 | 5 | [1, 1, 2, 2] |" in out.read_text(encoding="utf-8")


def test_equal_multi_hash_sets_are_not_counted_as_cross_host_identity(tmp_path: Path) -> None:
    a, b = _frame(), _frame(30)
    for name, system in (("mac", "Darwin"), ("linux", "Linux")):
        path = _evidence(tmp_path, name, system, {7: a, 11: _frame(20)})
        record = json.loads(path.read_text(encoding="utf-8"))
        alternating = [a, b, a, b, a]
        resets = [_reset(i, f) for i, f in enumerate(alternating)]
        record["per_seed"][0] = recorder.summarize_seed(7, resets, alternating)
        path.write_text(json.dumps(record), encoding="utf-8")
    out = tmp_path / "REPORT.md"
    report.main(
        ["mac.json", "linux.json", "--output", str(out), "--repository-root", str(tmp_path)]
    )
    text = out.read_text(encoding="utf-8")
    assert "Frame hash sets equal on 2 of 2 common seeds" in text
    assert "each host produced one frame hash and the two match: 1 of 2." in text
