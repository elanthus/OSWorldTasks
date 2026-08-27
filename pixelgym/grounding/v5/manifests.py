"""Content-bound v5 partition and environment manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import (
    GENERATOR_VERSION,
    PROTOCOL_VERSION,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    Partition,
    V5Task,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.generator import generate_task, tasks_for_partition
from pixelgym.grounding.v5.seeds import D56_REPLACEMENT_CALIBRATION_SEEDS

D56_PILOT_PLAN_DIGEST = (
    "sha256:fe6e9b03fd5b4c13d417596d1712073e2d375de03711a3aeca6e04cf2f55fd7a"
)
D56_EXCLUDED_CALIBRATION_SEEDS = (
    5100,
    5110,
    5120,
    5130,
    5140,
    5150,
    5101,
    5111,
    5121,
    5131,
)
D56_CONSUMED_CALIBRATION_SEEDS = (
    5102,
    5103,
    5104,
    5105,
    5106,
    5107,
    5108,
    5109,
)
D56_CONSUMED_CALIBRATION_PLAN_DIGEST = (
    "sha256:270d4b1941cac585fa51907df170463460e723639372c5f68eee5e1f888857d1"
)
D56_CONSUMED_CALIBRATION_SUMMARY_SHA256 = (
    "sha256:65bb9d293eed69d480718b9d6380b1c87ae07216606927b2f45b7118dd74f10c"
)
D56_SUPERSEDED_MANIFEST_DIGEST = (
    "sha256:f48c28cb252f6a88eabc29a3962d9a8ff8a3c93217b6222202266bc8ec5244c9"
)


@dataclass(frozen=True)
class EnvironmentManifest:
    backend_identity: str
    app_url: str
    dependency_lock_digest: str
    code_revision: str
    runtime_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pixelgym-agent-v5-environment-v1",
            "protocol_version": PROTOCOL_VERSION,
            "backend_identity": self.backend_identity,
            "app_url": self.app_url,
            "screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
            "dependency_lock_digest": self.dependency_lock_digest,
            "code_revision": self.code_revision,
            "runtime_digest": self.runtime_digest,
        }


def partition_manifest(partition: Partition) -> dict[str, Any]:
    tasks = tasks_for_partition(partition)
    records = [
        {
            "seed_record": task.seed_record.to_dict(),
            "task_id": task.task_id,
            "task_digest": content_digest(task.canonical_dict()),
            "semantic_digest": task.semantic_digest,
            "max_episode_steps": task.max_episode_steps,
            "initial_capture_required": True,
        }
        for task in tasks
    ]
    manifest = {
        "schema_version": "pixelgym-agent-v5-partition-v2",
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generator_source_digest": generator_source_digest(),
        "partition": partition.value,
        "episode_count": len(records),
        "records": records,
    }
    return {**manifest, "manifest_digest": content_digest(manifest)}


def d56_calibration_manifest() -> dict[str, Any]:
    """Derive a fresh D5.6 set after the consumed pilot and partial panel run."""

    source = partition_manifest(Partition.CALIBRATION)
    excluded_seed_order = (
        *D56_EXCLUDED_CALIBRATION_SEEDS,
        *D56_CONSUMED_CALIBRATION_SEEDS,
    )
    excluded_seeds = set(excluded_seed_order)
    source_by_seed = {
        record["seed_record"]["seed"]: record for record in source["records"]
    }
    excluded = [source_by_seed[seed] for seed in excluded_seed_order]
    records = [
        record
        for record in source["records"]
        if record["seed_record"]["seed"] not in excluded_seeds
    ]
    replacement_records = [
        _task_manifest_record(generate_task(seed))
        for seed in D56_REPLACEMENT_CALIBRATION_SEEDS
    ]
    records.extend(replacement_records)
    if len(excluded) != len(excluded_seed_order):
        raise ValueError("D5.6 exclusions do not match the frozen calibration source")
    if len(records) != 50:
        raise ValueError("D5.6 calibration derivation must retain fifty episodes")
    manifest = {
        "schema_version": "pixelgym-agent-v5-partition-v4",
        "protocol_version": source["protocol_version"],
        "generator_version": source["generator_version"],
        "generator_source_digest": source["generator_source_digest"],
        "partition": Partition.CALIBRATION.value,
        "episode_count": len(records),
        "records": records,
        "derivation": {
            "source_manifest_digest": source["manifest_digest"],
            "supersedes_manifest_digest": D56_SUPERSEDED_MANIFEST_DIGEST,
            "excluded_episode_count": len(excluded),
            "excluded_seeds": list(excluded_seed_order),
            "excluded_task_ids": [record["task_id"] for record in excluded],
            "exclusion_rule": (
                "exclude every task assigned to the approved Qwen two-action pilot and every "
                "task exposed by the consumed partial four-policy calibration, independent of "
                "outcome"
            ),
            "pilot_plan_digest": D56_PILOT_PLAN_DIGEST,
            "consumed_calibration_plan_digest": (
                D56_CONSUMED_CALIBRATION_PLAN_DIGEST
            ),
            "consumed_calibration_summary_sha256": (
                D56_CONSUMED_CALIBRATION_SUMMARY_SHA256
            ),
            "replacement_episode_count": len(replacement_records),
            "replacement_seeds": list(D56_REPLACEMENT_CALIBRATION_SEEDS),
            "replacement_task_ids": [
                record["task_id"] for record in replacement_records
            ],
        },
    }
    return {**manifest, "manifest_digest": content_digest(manifest)}


def _task_manifest_record(task: V5Task) -> dict[str, Any]:
    return {
        "seed_record": task.seed_record.to_dict(),
        "task_id": task.task_id,
        "task_digest": content_digest(task.canonical_dict()),
        "semantic_digest": task.semantic_digest,
        "max_episode_steps": task.max_episode_steps,
        "initial_capture_required": True,
    }


def generator_source_digest() -> str:
    package = Path(__file__).parent
    files = ("contracts.py", "generator.py", "seeds.py")
    content = b"".join(
        name.encode("utf-8") + b"\0" + (package / name).read_bytes() + b"\0"
        for name in files
    )
    return "sha256:" + sha256_bytes(content)
