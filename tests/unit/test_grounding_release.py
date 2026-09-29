from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts.release_observations import (
    RELEASE_OBSERVATIONS_SCHEMA_VERSION,
    collect_release_observations,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def test_release_collector_reports_raw_counts_without_a_verdict(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    (tmp_path / "README.md").write_text("metric [PENDING final]\n")
    artifacts.mkdir()
    (artifacts / "grounding-protocol.md").write_text("frozen\n")
    examples = [
        {
            "example_id": "example-1",
            "protocol_version": "v1",
            "screen_state": "initial",
            "element_type": "button",
        }
    ]
    overlays = [{"example_id": "example-1", "target_proposed": True}]
    predictions = [
        {
            "example_id": "example-1",
            "condition": condition,
            "parse_status": "parsed",
            "request_failure": None,
            "provider": "provider",
            "model": "model",
            "protocol_version": "v1",
            "prompt_version": "p1",
            "timestamp_utc": f"2026-01-01T00:00:0{index}Z",
        }
        for index, condition in enumerate(("raw", "marks"))
    ]
    _write_jsonl(artifacts / "grounding-dataset.jsonl", examples)
    _write_jsonl(artifacts / "grounding-overlays.jsonl", overlays)
    _write_jsonl(artifacts / "grounding-predictions.jsonl", predictions)
    _write_json(
        artifacts / "day-2/raw/reward-hacking.json",
        {
            "summary": {"attack_count": 1},
            "attacks": [{"disposition": "tested"}],
            "known_limitations": ["limit"],
        },
    )

    observations = collect_release_observations(tmp_path)

    assert observations["schema_version"] == RELEASE_OBSERVATIONS_SCHEMA_VERSION
    assert observations["verdict"] is None
    assert observations["predictions"]["condition_record_count"] == 2
    assert observations["predictions"]["example_count_with_two_records"] == 1
    assert observations["dataset"]["target_proposed_count"] == 1
    assert observations["day2"]["reward_hacking"]["known_limitation_count"] == 1
    assert observations["portfolio_copy"]["public_readme"]["placeholder_counts"] == {"[PENDING": 1}
    required = {row["path"]: row for row in observations["required_artifacts"]}
    assert required["artifacts/grounding-protocol.md"]["exists"] is True
    assert required["artifacts/grounding-results.json"]["exists"] is False


def test_release_collector_is_build_time_only_and_reads_no_resume_copy(tmp_path: Path) -> None:
    assert importlib.util.find_spec("pixelgym.grounding.release") is None
    (tmp_path / "artifacts/day-3/review").mkdir(parents=True)
    (tmp_path / "artifacts/resume-bullets.md").write_text("[PENDING]\n")
    (tmp_path / "artifacts/day-3/review/resume-bullets-draft.md").write_text("[PENDING]\n")

    copy = collect_release_observations(tmp_path)["portfolio_copy"]

    assert set(copy) == {"public_readme", "readme_review_draft"}
    assert not any("resume" in row["path"] for row in copy.values())
