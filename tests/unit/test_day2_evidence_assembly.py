from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_day2 import _assemble

_RAW_FILES = (
    "fake-reset.json",
    "real-reset.json",
    "reward-timing.json",
    "space-integrity.json",
    "browser-boundary.json",
    "real-space-smoke.json",
    "reward-hacking.json",
    "real-golden-episode.json",
)


def _write_sections(raw_dir: Path, *, include_browser: bool) -> None:
    raw_dir.mkdir(parents=True)
    for name in _RAW_FILES:
        if name == "browser-boundary.json" and not include_browser:
            continue
        (raw_dir / name).write_text(json.dumps({"summary": {"passed": True}}), encoding="utf-8")


def test_day2_assembly_requires_stored_browser_boundary_evidence(tmp_path: Path) -> None:
    raw_dir = tmp_path / "missing-browser/raw"
    _write_sections(raw_dir, include_browser=False)

    report = _assemble(
        raw_dir,
        tmp_path / "missing-browser/report.json",
        tmp_path / "missing-browser/preparation.json",
    )

    assert report["automated_validation"]["status"] == "INCOMPLETE"
    assert report["automated_validation"]["missing_sections"] == ["browser_boundary"]


def test_day2_assembly_counts_browser_boundary_as_required_evidence(tmp_path: Path) -> None:
    raw_dir = tmp_path / "complete/raw"
    _write_sections(raw_dir, include_browser=True)

    report = _assemble(
        raw_dir,
        tmp_path / "complete/report.json",
        tmp_path / "complete/preparation.json",
    )

    assert report["automated_validation"]["status"] == "PASS"
    assert report["automated_validation"]["completed_section_count"] == 8
    assert report["automated_validation"]["required_section_count"] == 8
