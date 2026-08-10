"""Raw Day 3 release observations with no automated gate verdict."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pixelgym.grounding.overlays import load_jsonl

RELEASE_OBSERVATIONS_SCHEMA_VERSION = "pixelgym-day3-release-observations-v1"

_REQUIRED_ARTIFACTS = (
    "artifacts/grounding-protocol.md",
    "artifacts/grounding-dataset.jsonl",
    "artifacts/grounding-predictions.jsonl",
    "artifacts/grounding-results.json",
    "artifacts/grounding-report.md",
    "artifacts/grounding/figures/raw-vs-marks-accuracy.png",
    "artifacts/grounding/figures/control-type-accuracy.png",
    "artifacts/grounding/gallery/manifest.json",
)
_PLACEHOLDER_MARKERS = (
    "[PENDING",
    "[DELTA]",
    "[LOW]",
    "[HIGH]",
    "[MODEL/DATE]",
    "[COVERAGE]",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_observation(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {
        "path": relative,
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.is_file() else None


def _dataset_observations(root: Path) -> dict[str, Any] | None:
    dataset_path = root / "artifacts" / "grounding-dataset.jsonl"
    overlays_path = root / "artifacts" / "grounding-overlays.jsonl"
    if not dataset_path.is_file() or not overlays_path.is_file():
        return None
    examples = load_jsonl(dataset_path)
    overlays = load_jsonl(overlays_path)
    example_ids = [row["example_id"] for row in examples]
    overlay_ids = [row["example_id"] for row in overlays]
    return {
        "example_count": len(examples),
        "unique_example_id_count": len(set(example_ids)),
        "overlay_count": len(overlays),
        "unique_overlay_example_id_count": len(set(overlay_ids)),
        "dataset_overlay_id_sets_equal": set(example_ids) == set(overlay_ids),
        "protocol_versions": sorted({row["protocol_version"] for row in examples}),
        "screen_state_counts": dict(
            sorted(Counter(row["screen_state"] for row in examples).items())
        ),
        "element_type_counts": dict(
            sorted(Counter(row["element_type"] for row in examples).items())
        ),
        "target_proposed_count": sum(row["target_proposed"] is True for row in overlays),
        "target_not_proposed_count": sum(row["target_proposed"] is not True for row in overlays),
    }


def _prediction_observations(root: Path, dataset: dict[str, Any] | None) -> dict[str, Any] | None:
    path = root / "artifacts" / "grounding-predictions.jsonl"
    if not path.is_file():
        return None
    records = load_jsonl(path)
    keys = [(row["example_id"], row["condition"]) for row in records]
    examples = {row["example_id"] for row in records}
    paired = Counter(row["example_id"] for row in records)
    expected_examples = dataset["example_count"] if dataset else None
    return {
        "condition_record_count": len(records),
        "unique_example_condition_count": len(set(keys)),
        "unique_example_count": len(examples),
        "example_count_with_two_records": sum(count == 2 for count in paired.values()),
        "example_count_without_two_records": sum(count != 2 for count in paired.values()),
        "dataset_example_count": expected_examples,
        "condition_counts": dict(sorted(Counter(row["condition"] for row in records).items())),
        "parse_status_counts": dict(
            sorted(Counter(row["parse_status"] for row in records).items())
        ),
        "request_failure_count": sum(row["request_failure"] is not None for row in records),
        "provider_values": sorted({row["provider"] for row in records}),
        "model_values": sorted({row["model"] for row in records}),
        "protocol_versions": sorted({row["protocol_version"] for row in records}),
        "prompt_versions": sorted({row["prompt_version"] for row in records}),
        "first_timestamp_utc": min(row["timestamp_utc"] for row in records),
        "last_timestamp_utc": max(row["timestamp_utc"] for row in records),
    }


def _results_observations(root: Path) -> dict[str, Any] | None:
    path = root / "artifacts" / "grounding-results.json"
    results = _load_json(path)
    if results is None:
        return None
    input_hash_observations = {}
    for relative, recorded_hash in results.get("inputs", {}).items():
        input_path = root / relative
        input_hash_observations[relative] = {
            "exists": input_path.is_file(),
            "recorded_sha256": recorded_hash,
            "current_sha256": _sha256(input_path) if input_path.is_file() else None,
            "hashes_equal": input_path.is_file() and _sha256(input_path) == recorded_hash,
        }
    output_hash_observations = {}
    for name, output in results.get("outputs", {}).items():
        if not isinstance(output, dict) or "path" not in output or "sha256" not in output:
            continue
        output_path = root / output["path"]
        output_hash_observations[name] = {
            "path": output["path"],
            "exists": output_path.is_file(),
            "recorded_sha256": output["sha256"],
            "current_sha256": _sha256(output_path) if output_path.is_file() else None,
            "hashes_equal": output_path.is_file() and _sha256(output_path) == output["sha256"],
        }
    return {
        "schema_version": results.get("schema_version"),
        "protocol_version": results.get("protocol_version"),
        "prompt_version": results.get("prompt_version"),
        "provider": results.get("provider"),
        "model": results.get("model"),
        "collection": results.get("collection"),
        "conditions": results.get("conditions"),
        "paired": results.get("paired"),
        "set_of_marks": results.get("set_of_marks"),
        "error_record_count": results.get("error_taxonomy", {}).get("error_record_count"),
        "error_review_status_counts": results.get("error_taxonomy", {}).get("review_status_counts"),
        "all_errors_manually_reviewed": results.get("error_taxonomy", {}).get(
            "all_manually_reviewed"
        ),
        "input_hash_observations": input_hash_observations,
        "output_hash_observations": output_hash_observations,
    }


def _copy_observations(root: Path) -> dict[str, Any]:
    paths = {
        "public_readme": root / "README.md",
        "readme_review_draft": root / "artifacts/day-3/review/README-draft.md",
        "resume_final": root / "artifacts/resume-bullets.md",
        "resume_review_draft": root / "artifacts/day-3/review/resume-bullets-draft.md",
    }
    output = {}
    for name, path in paths.items():
        text = path.read_text() if path.is_file() else ""
        output[name] = {
            "path": path.relative_to(root).as_posix(),
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
            "placeholder_counts": {
                marker: text.count(marker) for marker in _PLACEHOLDER_MARKERS if marker in text
            },
        }
    return output


def _day2_observations(root: Path) -> dict[str, Any]:
    validation = _load_json(root / "artifacts/validation-report.json")
    episode = _load_json(root / "artifacts/day-2/raw/real-golden-episode.json")
    audit = _load_json(root / "artifacts/day-2/raw/reward-hacking.json")
    return {
        "validation_report": None
        if validation is None
        else {
            "automated_validation": validation.get("automated_validation"),
            "human_gate": validation.get("human_gate"),
            "gate_disclaimer": validation.get("gate_disclaimer"),
        },
        "real_golden_episode": None
        if episode is None
        else {
            "summary": episode.get("summary"),
            "action_count": episode.get("action_count"),
            "backend_metadata": episode.get("backend_metadata"),
        },
        "reward_hacking": None
        if audit is None
        else {
            "summary": audit.get("summary"),
            "known_limitation_count": len(audit.get("known_limitations", [])),
            "disposition_counts": dict(
                sorted(Counter(row["disposition"] for row in audit.get("attacks", [])).items())
            ),
        },
    }


def _demo_observations(root: Path) -> dict[str, Any] | None:
    metadata_path = root / "artifacts/day-3/review/real-osworld-episode.json"
    metadata = _load_json(metadata_path)
    if metadata is None:
        return None
    output_path = root / metadata["output_path"]
    return {
        "metadata_path": metadata_path.relative_to(root).as_posix(),
        "metadata_sha256": _sha256(metadata_path),
        "output_path": metadata["output_path"],
        "output_exists": output_path.is_file(),
        "recorded_output_sha256": metadata["output_sha256"],
        "current_output_sha256": _sha256(output_path) if output_path.is_file() else None,
        "output_hashes_equal": output_path.is_file()
        and _sha256(output_path) == metadata["output_sha256"],
        "duration_seconds": metadata["duration_seconds"],
        "source_frame_count": metadata["source_frame_count"],
        "positive_reward_count": metadata["positive_reward_count"],
        "terminal_reward": metadata["terminal_reward"],
        "credential_or_private_ui_review": metadata["credential_or_private_ui_review"],
        "approved_for_public_readme": metadata["approved_for_public_readme"],
    }


def collect_release_observations(repository_root: Path) -> dict[str, Any]:
    """Collect raw evidence only; the project owner decides the D3.11 verdict."""
    dataset = _dataset_observations(repository_root)
    return {
        "schema_version": RELEASE_OBSERVATIONS_SCHEMA_VERSION,
        "gate": "D3.11",
        "verdict": None,
        "verdict_owner": "project owner",
        "required_artifacts": [
            _file_observation(repository_root, relative) for relative in _REQUIRED_ARTIFACTS
        ],
        "dataset": dataset,
        "predictions": _prediction_observations(repository_root, dataset),
        "results": _results_observations(repository_root),
        "day2": _day2_observations(repository_root),
        "portfolio_copy": _copy_observations(repository_root),
        "demo": _demo_observations(repository_root),
        "human_approvals": _load_json(repository_root / "artifacts/day-3/raw/human-approvals.json"),
        "protocol_provenance": _load_json(
            repository_root / "artifacts/day-3/raw/protocol-provenance.json"
        ),
        "clean_install_command_evidence": _load_json(
            repository_root / "artifacts/day-3/release/clean-install.json"
        ),
    }
