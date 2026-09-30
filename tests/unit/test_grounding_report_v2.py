"""Tests for the grounding report v2 (offline; reads frozen artifacts, writes only tmp_path)."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding import report_v2
from pixelgym.grounding.verification import verify_grounding_report

ROOT = Path(__file__).resolve().parents[2]
VERDICT_WORDS = ("confirmed", "confirms", "significant", "significance", "proves", "proven")
_NUMBER = re.compile(r"(?<![\w.\-/])[+-]?\d[\d,]*(?:\.\d+)?(?:e[+-]?\d+)?(?![\w-])")


@pytest.fixture(scope="module")
def inputs() -> dict[str, Any]:
    return report_v2.load_inputs(ROOT)


@pytest.fixture(scope="module")
def report_text(inputs: dict[str, Any]) -> str:
    return report_v2.render_report_v2(inputs)


def test_committed_v2_files_regenerate_byte_identically() -> None:
    report, provenance = report_v2.render_outputs(ROOT)
    assert (ROOT / report_v2.REPORT_PATH).read_text(encoding="utf-8") == report
    assert (ROOT / report_v2.PROVENANCE_PATH).read_text(encoding="utf-8") == provenance
    assert report_v2.verify_outputs(ROOT)["wrote_files"] is False


def test_v1_report_still_equals_fresh_v1_generation() -> None:
    result = verify_grounding_report(ROOT)
    assert result["status"] == "verified"


def test_provenance_records_input_digests_and_pull_request() -> None:
    provenance = json.loads((ROOT / report_v2.PROVENANCE_PATH).read_text(encoding="utf-8"))
    assert [row["path"] for row in provenance["inputs"]] == list(report_v2.INPUT_PATHS)
    assert provenance["applies_from"]["branch"] == "cleanup/wp19-grounding-report-v2"
    assert provenance["applies_from"]["pull_request"].startswith("elanthus/OSWorldTasks#")
    assert provenance["retains"]["report"] == "artifacts/grounding-report.md"
    assert provenance["model_calls"] == 0


def test_verifier_detects_drift_without_writing(tmp_path: Path) -> None:
    for relative in (
        *report_v2.INPUT_PATHS,
        *report_v2.CITED_PATHS,
        report_v2.REPORT_PATH,
        report_v2.PROVENANCE_PATH,
    ):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    report_v2.verify_outputs(tmp_path)
    tampered = tmp_path / report_v2.REPORT_PATH
    tampered.write_text(tampered.read_text(encoding="utf-8") + "extra\n", encoding="utf-8")
    before = tampered.read_bytes()
    with pytest.raises(report_v2.GroundingReportV2Error, match="grounding-report-v2.md"):
        report_v2.verify_outputs(tmp_path)
    assert tampered.read_bytes() == before


def _collect_numbers(value: Any, out: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        for token in _NUMBER.findall(value):
            out.add(float(token.replace(",", "")))
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_numbers(key, out)
            _collect_numbers(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_numbers(item, out)


def _matches(token: str, sources: set[float]) -> bool:
    value = float(token.replace(",", "").lstrip("+"))
    if "e" in token.lower():
        return any(x and abs(x - value) <= abs(x) * 1e-3 for x in sources)
    decimals = len(token.split(".")[1]) if "." in token else 0
    candidates = {abs(value), value}
    return any(
        round(x, decimals) in candidates or round(100 * x, decimals) in candidates for x in sources
    )


def test_every_number_in_v2_markdown_is_in_an_input(
    inputs: dict[str, Any], report_text: str
) -> None:
    sources: set[float] = set()
    _collect_numbers(inputs, sources)
    # Candidate count per example is a derived count of overlay marks.
    sources.update(float(len(row["marks"])) for row in inputs["overlays"])
    # Excluded + retained total and error-record counts are derived counts of input rows.
    sources.add(float(len(inputs["error_review"])))
    sources.add(float(len(inputs["decisions"]["category_assignments"])))
    text = re.sub(r"`[^`]*`", " ", report_text)  # identifiers, paths, versions
    text = re.sub(r"\]\([^)]*\)", "]", text)  # link targets
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    haiku = f"scored {report_v2.HISTORICAL_HAIKU_RAW_TEXT} raw"
    assert haiku in text
    text = text.replace(haiku, "scored raw")  # checked against its cited source below
    tokens = _NUMBER.findall(text)
    assert tokens
    missing = [token for token in tokens if not _matches(token, sources)]
    assert missing == []


def test_haiku_figure_is_quoted_from_the_cited_historical_report() -> None:
    cited = (ROOT / report_v2.HISTORICAL_V3_REPORT_PATH).read_text(encoding="utf-8")
    assert f"**Claude Haiku 4.5:** Raw accuracy {report_v2.HISTORICAL_HAIKU_RAW_TEXT}" in cited


def test_required_statements_and_wording(report_text: str) -> None:
    lowered = report_text.lower()
    assert "paired targets" not in lowered
    assert "100 paired examples (10 target controls x 10 seeds)" in report_text
    for word in VERDICT_WORDS:
        assert re.search(rf"\b{word}\b", lowered) is None, word
    assert "| Example-level bootstrap | example | 100 | [+35.0, +54.0] |" in report_text
    assert "| Target-clustered bootstrap | target control | 10 | [+21.0, +67.0] |" in report_text
    assert "neither is the single result" in report_text
    assert "0 examples were excluded (n=100 of 100 retained)" in report_text
    assert "selection among 10 labelled candidates" in report_text
    assert "grounding-protocol.md#retention-validation-and-exclusions" in report_text
    assert "11909.31" in report_text and "11915.26" in report_text
    assert "manually reviewed error taxonomy" not in report_text
    assert "No examples were excluded." not in report_text
    assert "2026-" not in report_text  # no run dates
    for key in ("bootstrap_samples", "percentile_convention", "rng", "cluster_level_draw_order"):
        assert f"- {key}: " in report_text


def test_exclusion_sentence_follows_the_data(inputs: dict[str, Any]) -> None:
    changed = copy.deepcopy(inputs["results"])
    changed["collection"]["excluded_example_count"] = 3
    changed["per_example"] = changed["per_example"][:97]
    assert "3 examples were excluded (n=97 of 100 retained)" in report_v2.exclusion_sentence(
        changed
    )
    changed["collection"]["excluded_example_count"] = 1
    assert "1 example was excluded (n=97 of 98 retained)" in report_v2.exclusion_sentence(changed)


def test_error_review_paragraph_follows_the_data(inputs: dict[str, Any]) -> None:
    reviews = copy.deepcopy(inputs["error_review"])
    decisions = copy.deepcopy(inputs["decisions"])
    reviews[0]["review_status"] = "pending"
    reviews[1]["reviewer"] = "reviewer-a"
    decisions["review_method"] = "Synthetic method."
    text = report_v2.error_review_paragraph(reviews, decisions)
    assert "1 `pending`" in text and "43 `manual_visual_review`" in text
    assert "Reviewer recorded: `reviewer-a`." in text
    assert 'Recorded review method: "Synthetic method."' in text
    del decisions["review_method"]
    assert "No review method is recorded." in report_v2.error_review_paragraph(reviews, decisions)
    original = report_v2.error_review_paragraph(inputs["error_review"], inputs["decisions"])
    assert "do not record a reviewer identity" in original


def test_inconsistent_inputs_are_rejected(inputs: dict[str, Any]) -> None:
    changed = copy.deepcopy(inputs)
    changed["clustered"]["delta_percentage_points"] = 43.0
    with pytest.raises(report_v2.GroundingReportV2Error, match="delta"):
        report_v2.render_report_v2(changed)
    changed = copy.deepcopy(inputs)
    changed["error_review"] = changed["error_review"][1:]
    with pytest.raises(report_v2.GroundingReportV2Error):
        report_v2.render_report_v2(changed)
