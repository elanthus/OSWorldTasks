"""Print a no-provider-call v5 cap plan for a checked-in policy manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.contracts import PolicyManifest, SandboxManifest
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.serialization import canonical_json_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--partition-manifest-directory", type=Path, required=True)
    parser.add_argument("--approved-calibration-partition-digest", required=True)
    args = parser.parse_args()
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    sandbox = SandboxManifest(**value.pop("sandbox"))
    value["inference_parameters"] = tuple(
        tuple(item) for item in value.get("inference_parameters", [])
    )
    manifest = PolicyManifest(sandbox=sandbox, **value)
    print(
        canonical_json_text(
            call_cap_plan(
                manifest,
                partition_manifests=load_partition_manifests(
                    args.partition_manifest_directory
                ),
                approved_calibration_manifest_digest=(
                    args.approved_calibration_partition_digest
                ),
            )
        )
    )


if __name__ == "__main__":
    main()
