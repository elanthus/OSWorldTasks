"""Free plan-only call-cap calculations for every v5 evaluation phase."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import (
    CallCaps,
    Partition,
    PolicyManifest,
    WorkflowFamily,
    content_digest,
)


def load_partition_manifests(directory: Path) -> dict[Partition, dict[str, Any]]:
    """Load the exact partition bytes selected for one immutable cap plan."""

    manifests: dict[Partition, dict[str, Any]] = {}
    for partition in Partition:
        value = json.loads((directory / f"{partition.value}.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError(f"{partition.value} partition manifest must be an object")
        manifests[partition] = value
    return manifests


def _validated_records(
    manifest: Mapping[str, Any], *, partition: Partition
) -> tuple[dict[str, Any], ...]:
    claimed_digest = manifest.get("manifest_digest")
    unsigned = dict(manifest)
    unsigned.pop("manifest_digest", None)
    if not isinstance(claimed_digest, str) or content_digest(unsigned) != claimed_digest:
        raise ValueError(f"{partition.value} partition manifest digest mismatch")
    if manifest.get("schema_version") != "pixelgym-agent-v5-partition-v1":
        raise ValueError(f"{partition.value} partition manifest schema mismatch")
    if manifest.get("partition") != partition.value:
        raise ValueError(f"{partition.value} partition manifest identity mismatch")
    records = manifest.get("records")
    if not isinstance(records, list) or manifest.get("episode_count") != len(records):
        raise ValueError(f"{partition.value} partition manifest record count mismatch")

    validated: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise TypeError(f"{partition.value} partition record must be an object")
        seed_record = record.get("seed_record")
        if not isinstance(seed_record, dict) or seed_record.get("partition") != partition.value:
            raise ValueError(f"{partition.value} partition record identity mismatch")
        family = seed_record.get("family")
        if not isinstance(family, str):
            raise TypeError(f"{partition.value} partition record family must be text")
        try:
            WorkflowFamily(family)
        except ValueError as exc:
            raise ValueError(f"{partition.value} partition record family mismatch") from exc
        max_episode_steps = record.get("max_episode_steps")
        if type(max_episode_steps) is not int or max_episode_steps <= 0:
            raise ValueError(f"{partition.value} partition record action cap is invalid")
        validated.append(record)
    return tuple(validated)


def _family_stratified_subset(
    records: tuple[dict[str, Any], ...], *, per_family: int
) -> tuple[dict[str, Any], ...]:
    subset = tuple(
        record
        for family in WorkflowFamily
        for record in tuple(
            candidate
            for candidate in records
            if candidate["seed_record"]["family"] == family.value
        )[:per_family]
    )
    expected = len(WorkflowFamily) * per_family
    if len(subset) != expected:
        raise ValueError("confirmatory partition does not contain the required family allocation")
    return subset


def call_cap_plan(
    manifest: PolicyManifest,
    *,
    partition_manifests: Mapping[Partition, Mapping[str, Any]],
    approved_calibration_manifest_digest: str,
) -> dict[str, Any]:
    """Derive phase caps only from verified, explicitly supplied partition bytes."""

    if set(partition_manifests) != set(Partition):
        raise ValueError("call-cap planning requires all three partition manifests")
    records = {
        partition: _validated_records(partition_manifests[partition], partition=partition)
        for partition in Partition
    }
    calibration_manifest_digest = partition_manifests[Partition.CALIBRATION][
        "manifest_digest"
    ]
    if approved_calibration_manifest_digest != calibration_manifest_digest:
        raise ValueError("approved calibration partition manifest digest mismatch")
    calibration = records[Partition.CALIBRATION]
    confirmatory = records[Partition.CONFIRMATORY]
    stateless_subset = _family_stratified_subset(confirmatory, per_family=4)
    reliability_subset = _family_stratified_subset(confirmatory, per_family=2)

    def calculate(task_records: tuple[dict[str, Any], ...], *, repetitions: int = 1) -> dict[str, int]:
        steps = tuple(
            record["max_episode_steps"]
            for record in task_records
            for _ in range(repetitions)
        )
        return CallCaps.calculate(
            max_episode_steps=steps,
            max_model_attempts_per_action=manifest.max_model_attempts_per_action,
            max_cancellation_requests_per_attempt=(
                manifest.max_cancellation_requests_per_attempt
            ),
            max_reconciliation_requests_per_attempt=(
                manifest.max_reconciliation_requests_per_attempt
            ),
        ).to_dict()

    plan = {
        "schema_version": "pixelgym-agent-v5-call-cap-plan-v1",
        "policy_id": manifest.policy_id,
        "policy_manifest_digest": content_digest(manifest.to_dict()),
        "provider": manifest.provider,
        "model": manifest.model,
        "partition_manifest_digests": {
            partition.value: partition_manifests[partition]["manifest_digest"]
            for partition in Partition
        },
        "approved_calibration_partition_manifest_digest": (
            approved_calibration_manifest_digest
        ),
        "phases": {
            "calibration": calculate(calibration),
            "confirmatory_primary": calculate(confirmatory),
            "stateless_ablation": calculate(stateless_subset),
            "reliability_repeats": calculate(reliability_subset, repetitions=2),
        },
        "provider_calls_made": 0,
    }
    return {**plan, "plan_digest": content_digest(plan)}
