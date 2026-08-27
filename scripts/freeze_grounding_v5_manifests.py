"""Write the three no-cost, content-bound v5 partition manifests."""

from __future__ import annotations

import argparse
from pathlib import Path

from pixelgym.grounding.v5.contracts import Partition
from pixelgym.grounding.v5.manifests import d56_calibration_manifest, partition_manifest
from pixelgym.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--include-d56-calibration", action="store_true")
    mode.add_argument("--d56-calibration-only", action="store_true")
    args = parser.parse_args()
    outputs = {
        partition: args.output_directory / f"{partition.value}.json"
        for partition in Partition
    }
    d56_output = args.output_directory / "calibration-d56.json"
    all_outputs = [] if args.d56_calibration_only else [*outputs.values()]
    if args.include_d56_calibration or args.d56_calibration_only:
        all_outputs.append(d56_output)
    occupied = [str(path) for path in all_outputs if path.exists()]
    if occupied:
        raise RuntimeError(f"partition manifest outputs must be fresh: {occupied}")
    args.output_directory.mkdir(parents=True, exist_ok=True)
    if not args.d56_calibration_only:
        for partition, path in outputs.items():
            path.write_bytes(canonical_json_bytes(partition_manifest(partition)) + b"\n")
    if args.include_d56_calibration or args.d56_calibration_only:
        d56_output.write_bytes(canonical_json_bytes(d56_calibration_manifest()) + b"\n")


if __name__ == "__main__":
    main()
