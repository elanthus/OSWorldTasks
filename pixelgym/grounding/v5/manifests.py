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
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.generator import tasks_for_partition


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
            "initial_capture_required": True,
        }
        for task in tasks
    ]
    manifest = {
        "schema_version": "pixelgym-agent-v5-partition-v1",
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generator_source_digest": generator_source_digest(),
        "partition": partition.value,
        "episode_count": len(records),
        "records": records,
    }
    return {**manifest, "manifest_digest": content_digest(manifest)}


def generator_source_digest() -> str:
    package = Path(__file__).parent
    files = ("contracts.py", "generator.py", "seeds.py")
    content = b"".join(
        name.encode("utf-8") + b"\0" + (package / name).read_bytes() + b"\0"
        for name in files
    )
    return "sha256:" + sha256_bytes(content)
