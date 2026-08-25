"""Write the no-cost scripted policy manifest and exact phase cap plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.planning import call_cap_plan
from pixelgym.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.manifest == args.plan or args.manifest.exists() or args.plan.exists():
        raise RuntimeError("scripted policy outputs must be distinct fresh paths")
    manifest = scripted_policy_manifest()
    for path in (args.manifest, args.plan):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_bytes(canonical_json_bytes(manifest.to_dict()) + b"\n")
    args.plan.write_bytes(canonical_json_bytes(call_cap_plan(manifest)) + b"\n")


if __name__ == "__main__":
    main()
