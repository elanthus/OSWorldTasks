"""Write the three no-cost, content-bound v5 partition manifests."""

from __future__ import annotations

import argparse
from pathlib import Path

from pixelgym.grounding.v5.contracts import Partition
from pixelgym.grounding.v5.manifests import partition_manifest
from pixelgym.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    outputs = {
        partition: args.output_directory / f"{partition.value}.json"
        for partition in Partition
    }
    occupied = [str(path) for path in outputs.values() if path.exists()]
    if occupied:
        raise RuntimeError(f"partition manifest outputs must be fresh: {occupied}")
    args.output_directory.mkdir(parents=True, exist_ok=True)
    for partition, path in outputs.items():
        path.write_bytes(canonical_json_bytes(partition_manifest(partition)) + b"\n")


if __name__ == "__main__":
    main()
