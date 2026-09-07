"""The stored-evidence report renderer must accept every human-gate record shape."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_validation_report import render

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_REPORT = ROOT / "artifacts/validation-report.json"
REVISION_REPORT = ROOT / "artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_render_historical_gate_record_keeps_day3_authorization_line() -> None:
    markdown = render(_load(HISTORICAL_REPORT))

    assert "**Human D2.11 verdict: PASS.** Day 3 authorized: yes." in markdown
    assert "Declared by project owner at `2026-08-10T05:52:03Z`." in markdown


def test_render_regrade_record_without_day3_flag() -> None:
    markdown = render(_load(REVISION_REPORT))

    assert "**Human D2.11 verdict: PASS.**" in markdown
    assert "Day 3 authorized" not in markdown
    assert "Evidence revision graded: `artifacts/day-2-rev-2026-09-06-issues-95-101`." in markdown
    assert "Declared by project owner at `2026-09-07T06:12:43Z`." in markdown


def test_render_without_human_gate_omits_verdict() -> None:
    report = _load(REVISION_REPORT)
    report["human_gate"] = None

    markdown = render(report)

    assert "Human D2.11 verdict" not in markdown


def test_render_links_raw_evidence_relative_to_the_requested_prefix() -> None:
    markdown = render(_load(REVISION_REPORT), raw_link_prefix="raw")

    assert "- `real_reset`: [raw JSON](raw/real-reset.json)" in markdown
    assert "day-2/raw" not in markdown


def test_render_default_prefix_matches_the_historical_layout() -> None:
    markdown = render(_load(HISTORICAL_REPORT))

    assert "- `real_reset`: [raw JSON](day-2/raw/real-reset.json)" in markdown
