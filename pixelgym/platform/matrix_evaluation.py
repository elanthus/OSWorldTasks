"""Canonical contracts for plan-driven seed-by-policy evaluation fan-out."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pixelgym.platform.contracts import PolicyManifest
from pixelgym.platform.fingerprints import (
    build_dataset_manifest,
    canonical_json_bytes,
    sha256_bytes,
)
from pixelgym.platform.policy import verify_policy_manifest
from pixelgym.serialization import load_jsonl

PLAN_SCHEMA_VERSION = "pixelgym-seed-policy-plan-v1"
AGGREGATE_SCHEMA_VERSION = "pixelgym-seed-policy-aggregate-v1"
TIMING_SCHEMA_VERSION = "pixelgym-seed-policy-timing-v1"


def assignment_id(*, dataset_fingerprint: str, seed: int, policy_id: str) -> str:
    """Return the identity of one explicit seed-policy assignment."""
    material = {
        "dataset_fingerprint": dataset_fingerprint,
        "policy_id": policy_id,
        "seed": seed,
    }
    return "sha256:" + sha256_bytes(canonical_json_bytes(material))


def canonical_seed_policy_plan(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an explicit matrix plan without enumerating a product."""
    if set(value) != {"schema_version", "dataset_fingerprint", "policies", "assignments"}:
        raise ValueError("seed-policy plan has unknown or missing top-level fields")
    if value.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("seed-policy plan has an unsupported schema version")
    dataset_fingerprint = value.get("dataset_fingerprint")
    if not isinstance(dataset_fingerprint, str):
        raise TypeError("seed-policy plan dataset fingerprint must be a string")
    digest = dataset_fingerprint.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("seed-policy plan requires a dataset fingerprint")
    policies_value = value.get("policies")
    assignments_value = value.get("assignments")
    if not isinstance(policies_value, list) or not policies_value:
        raise ValueError("seed-policy plan requires at least one policy")
    if not isinstance(assignments_value, list) or not assignments_value:
        raise ValueError("seed-policy plan requires explicit assignments")

    policies: list[dict[str, Any]] = []
    policy_ids: set[str] = set()
    for item in policies_value:
        if not isinstance(item, dict):
            raise TypeError("plan policies must be objects")
        manifest = PolicyManifest(**item)
        verify_policy_manifest(manifest)
        if manifest.policy_id in policy_ids:
            raise ValueError("plan policy IDs must be unique")
        policy_ids.add(manifest.policy_id)
        policies.append(manifest.to_dict())

    assignments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[int, str]] = set()
    for item in assignments_value:
        if not isinstance(item, dict) or set(item) != {"assignment_id", "seed", "policy_id"}:
            raise ValueError("plan assignments require assignment_id, seed, and policy_id")
        seed = item["seed"]
        policy_id = item["policy_id"]
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("assignment seed must be a nonnegative integer")
        if policy_id not in policy_ids:
            raise ValueError("assignment references an unknown policy")
        expected_id = assignment_id(
            dataset_fingerprint=dataset_fingerprint,
            seed=seed,
            policy_id=policy_id,
        )
        if item["assignment_id"] != expected_id:
            raise ValueError("assignment digest mismatch")
        pair = (seed, policy_id)
        if expected_id in seen_ids or pair in seen_pairs:
            raise ValueError("seed-policy assignments must be unique")
        seen_ids.add(expected_id)
        seen_pairs.add(pair)
        assignments.append(dict(item))

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "dataset_fingerprint": dataset_fingerprint,
        "policies": sorted(policies, key=lambda item: item["policy_id"]),
        "assignments": sorted(assignments, key=lambda item: item["assignment_id"]),
    }


def seed_policy_plan_digest(plan: dict[str, Any]) -> str:
    canonical = canonical_seed_policy_plan(plan)
    return "sha256:" + sha256_bytes(canonical_json_bytes(canonical))


def load_seed_policy_plan(path: Path, *, repository_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("seed-policy plan is not readable canonical JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("seed-policy plan root must be an object")
    plan = canonical_seed_policy_plan(value)
    _, actual_fingerprint = build_dataset_manifest(
        repository_root=repository_root,
        dataset_path=repository_root / "artifacts/grounding-dataset.jsonl",
        overlays_path=repository_root / "artifacts/grounding-overlays.jsonl",
    )
    if plan["dataset_fingerprint"] != actual_fingerprint:
        raise ValueError("seed-policy plan dataset fingerprint differs from the frozen dataset")
    available_seeds = {
        row["task_seed"] for row in load_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")
    }
    requested_seeds = {item["seed"] for item in plan["assignments"]}
    if not requested_seeds <= available_seeds:
        raise ValueError("seed-policy plan references a seed absent from the frozen dataset")
    return plan


def example_ids_for_seed(repository_root: Path, seed: int) -> list[str]:
    identifiers = sorted(
        row["example_id"]
        for row in load_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")
        if row["task_seed"] == seed
    )
    if not identifiers:
        raise ValueError("assignment seed has no frozen examples")
    return identifiers


def canonical_seed_policy_aggregate(
    plan: dict[str, Any], branch_results: list[dict[str, Any]]
) -> bytes:
    """Return stable aggregate bytes, explicitly excluding branch timing fields."""
    normalized = canonical_seed_policy_plan(plan)
    expected = {item["assignment_id"]: item for item in normalized["assignments"]}
    actual: dict[str, dict[str, Any]] = {}
    for result in branch_results:
        if not isinstance(result, dict):
            raise TypeError("branch results must be objects")
        content = result.get("content")
        if not isinstance(content, dict):
            raise TypeError("branch result is missing canonical content")
        identifier = content.get("assignment_id")
        if not isinstance(identifier, str):
            raise TypeError("branch assignment ID must be a string")
        if identifier in actual:
            raise ValueError("joined branches contain a duplicate assignment")
        if identifier not in expected:
            raise ValueError("joined branches contain an unknown assignment")
        assignment = expected[identifier]
        if content.get("seed") != assignment["seed"] or content.get("policy_id") != assignment[
            "policy_id"
        ]:
            raise ValueError("branch result identity differs from its plan assignment")
        records = content.get("records")
        if not isinstance(records, list) or not records:
            raise ValueError("branch result must retain all assignment records")
        example_ids = [record.get("example_id") for record in records]
        if example_ids != sorted(set(example_ids)):
            raise ValueError("branch records must be canonical and unique")
        actual[identifier] = content
    if set(actual) != set(expected):
        raise ValueError("joined branches do not contain every assignment exactly once")
    aggregate = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "plan_digest": seed_policy_plan_digest(normalized),
        "dataset_fingerprint": normalized["dataset_fingerprint"],
        "assignments": [actual[identifier] for identifier in sorted(actual)],
    }
    return canonical_json_bytes(aggregate) + b"\n"


def _milliseconds(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000


def summarize_parallel_timing(raw: dict[str, Any]) -> dict[str, Any]:
    """Derive serial-equivalent work and observed overlap only from stored timestamps."""
    timings = raw.get("branches")
    if not isinstance(timings, list) or not timings:
        raise ValueError("timing evidence requires branch measurements")
    intervals: list[tuple[datetime, datetime, str]] = []
    runtime_ms = 0.0
    queue_ms = 0.0
    for branch in timings:
        start = datetime.fromisoformat(branch["started_at_utc"])
        end = datetime.fromisoformat(branch["ended_at_utc"])
        if end < start:
            raise ValueError("branch timing ends before it starts")
        intervals.append((start, end, branch["assignment_id"]))
        runtime_ms += float(branch["runtime_duration_ms"])
        queue_ms += float(branch["queue_duration_ms"])

    events: list[tuple[datetime, int]] = []
    for start, end, _ in intervals:
        events.extend(((start, 1), (end, -1)))
    active = 0
    max_parallel = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        max_parallel = max(max_parallel, active)
    overlap_pairs = sum(
        first_start < second_end and second_start < first_end
        for index, (first_start, first_end, _) in enumerate(intervals)
        for second_start, second_end, _ in intervals[index + 1 :]
    )
    branch_window_ms = (
        max(end for _, end, _ in intervals) - min(start for start, _, _ in intervals)
    ).total_seconds() * 1000
    return {
        "schema_version": TIMING_SCHEMA_VERSION,
        "plan_digest": raw["plan_digest"],
        "branch_count": len(intervals),
        "serial_equivalent_branch_runtime_ms": round(runtime_ms, 3),
        "observed_branch_window_ms": round(branch_window_ms, 3),
        "observed_total_wall_ms": round(
            _milliseconds(raw["flow_started_at_utc"], raw["flow_ended_at_utc"]), 3
        ),
        "join_duration_ms": round(
            _milliseconds(raw["join_started_at_utc"], raw["join_ended_at_utc"]), 3
        ),
        "total_queue_duration_ms": round(queue_ms, 3),
        "maximum_observed_parallel_branches": max_parallel,
        "overlapping_branch_pairs": overlap_pairs,
        "overlap_demonstrated": overlap_pairs > 0,
    }
