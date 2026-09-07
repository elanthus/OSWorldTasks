"""Tests for `pixelgym.grounding.v5.planning`'s file loader and versioned-schema
derivation/replacement validation (issue #170, coverage-gate follow-up).

`tests/unit/test_grounding_v5_runner.py` already exercises `call_cap_plan()` against
schema-v2 partition manifests, but never against `load_partition_manifests()` itself,
nor against the schema-v3/v4 "derived calibration" branches that only the checked-in
D5.6 manifest (and the now-deleted legacy calibration drivers) used. The shipped
`d56_calibration_manifest()` builder reproduces that exact derived shape, so these
exercise it directly with no `legacy` import.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.contracts import Partition, PolicyManifest, content_digest
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.manifests import d56_calibration_manifest, partition_manifest
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest

ROOT = Path(__file__).parents[1]


def policy_manifest() -> PolicyManifest:
    sandbox = build_sandbox_manifest(
        runtime_digest="sha256:" + "1" * 64,
        provider_endpoint="http://127.0.0.1:9999",
    )
    return PolicyManifest.build(
        provider="fake",
        model="no-cost-scripted-policy",
        exact_snapshot=True,
        harness_digest="sha256:" + "2" * 64,
        dependency_lock_digest="sha256:" + "3" * 64,
        system_prompt_digest="sha256:" + "4" * 64,
        task_renderer_version="v1",
        response_schema_version="v1",
        state_reducer_version="v1",
        parser_version="v1",
        memory_policy_version="stateful-v1",
        coordinate_adapter=IDENTITY_ADAPTER.name,
        coordinate_adapter_digest=IDENTITY_ADAPTER.source_digest,
        coordinate_input_convention="1024x768",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=1,
        max_reconciliation_requests_per_attempt=1,
        request_deadline_seconds=5.0,
        cancellation_mode="cancel-once",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision="test-revision",
        dirty_worktree_policy="reject",
    )


def _resign(manifest: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(manifest)
    unsigned.pop("manifest_digest", None)
    manifest["manifest_digest"] = content_digest(unsigned)
    return manifest


def test_load_partition_manifests_reads_exact_bytes_for_every_partition(
    tmp_path: Path,
) -> None:
    written = {partition: partition_manifest(partition) for partition in Partition}
    for partition, manifest in written.items():
        (tmp_path / f"{partition.value}.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    loaded = load_partition_manifests(tmp_path)

    assert loaded == written


def test_load_partition_manifests_uses_the_explicit_calibration_override(
    tmp_path: Path,
) -> None:
    for partition in (Partition.DEVELOPMENT, Partition.CONFIRMATORY):
        (tmp_path / f"{partition.value}.json").write_text(
            json.dumps(partition_manifest(partition)), encoding="utf-8"
        )
    override_path = tmp_path / "calibration-d56.json"
    override = d56_calibration_manifest()
    override_path.write_text(json.dumps(override), encoding="utf-8")

    loaded = load_partition_manifests(tmp_path, calibration_manifest=override_path)

    assert loaded[Partition.CALIBRATION] == override


def test_load_partition_manifests_rejects_a_non_object_manifest(tmp_path: Path) -> None:
    for partition in Partition:
        (tmp_path / f"{partition.value}.json").write_text(
            json.dumps(partition_manifest(partition)), encoding="utf-8"
        )
    (tmp_path / f"{Partition.CONFIRMATORY.value}.json").write_text(
        json.dumps([1, 2, 3]), encoding="utf-8"
    )

    with pytest.raises(TypeError, match="confirmatory partition manifest must be an object"):
        load_partition_manifests(tmp_path)


def _partitions_with_derived_calibration() -> dict[Partition, dict[str, Any]]:
    return {
        Partition.CALIBRATION: d56_calibration_manifest(),
        Partition.CONFIRMATORY: partition_manifest(Partition.CONFIRMATORY),
        Partition.DEVELOPMENT: partition_manifest(Partition.DEVELOPMENT),
    }


def test_call_cap_plan_accepts_a_v4_derived_calibration_partition() -> None:
    partitions = _partitions_with_derived_calibration()

    plan = call_cap_plan(
        policy_manifest(),
        partition_manifests=partitions,
        approved_calibration_manifest_digest=(
            partitions[Partition.CALIBRATION]["manifest_digest"]
        ),
    )

    assert plan["partition_manifest_digests"]["calibration"] == (
        partitions[Partition.CALIBRATION]["manifest_digest"]
    )
    assert plan["phases"]["calibration"]["environment_action_cap"] == sum(
        record["max_episode_steps"]
        for record in partitions[Partition.CALIBRATION]["records"]
    )


def test_call_cap_plan_rejects_a_derivation_with_a_wrong_excluded_count() -> None:
    partitions = _partitions_with_derived_calibration()
    revised = deepcopy(partitions[Partition.CALIBRATION])
    revised["derivation"]["excluded_episode_count"] += 1
    partitions[Partition.CALIBRATION] = _resign(revised)

    with pytest.raises(
        ValueError, match="derived calibration exclusion evidence is inconsistent"
    ):
        call_cap_plan(
            policy_manifest(),
            partition_manifests=partitions,
            approved_calibration_manifest_digest=(
                partitions[Partition.CALIBRATION]["manifest_digest"]
            ),
        )


def test_call_cap_plan_rejects_a_replacement_seed_outside_the_retained_records() -> None:
    partitions = _partitions_with_derived_calibration()
    revised = deepcopy(partitions[Partition.CALIBRATION])
    revised["derivation"]["replacement_seeds"].append(999_999)
    revised["derivation"]["replacement_task_ids"].append("v5-not-a-retained-task")
    revised["derivation"]["replacement_episode_count"] += 1
    partitions[Partition.CALIBRATION] = _resign(revised)

    with pytest.raises(
        ValueError, match="derived calibration replacement evidence is inconsistent"
    ):
        call_cap_plan(
            policy_manifest(),
            partition_manifests=partitions,
            approved_calibration_manifest_digest=(
                partitions[Partition.CALIBRATION]["manifest_digest"]
            ),
        )
