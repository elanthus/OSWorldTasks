"""Write the no-cost scripted policy manifest and exact phase cap plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from pixelgym.grounding.v5.fixtures import scripted_policy_manifest
from pixelgym.grounding.v5.planning import call_cap_plan
from pixelgym.serialization import canonical_json_bytes


def fresh_output_paths(manifest: Path, plan: Path) -> tuple[Path, Path]:
    manifest_path = manifest.resolve()
    plan_path = plan.resolve()
    if manifest_path == plan_path or manifest_path.exists() or plan_path.exists():
        raise RuntimeError("scripted policy outputs must be distinct fresh paths")
    return manifest_path, plan_path


def write_fresh_outputs(outputs: tuple[tuple[Path, bytes], ...]) -> None:
    created: list[Path] = []
    try:
        for path, data in outputs:
            with path.open("xb") as handle:
                created.append(path)
                handle.write(data)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    manifest_path, plan_path = fresh_output_paths(args.manifest, args.plan)
    manifest = scripted_policy_manifest()
    for path in (manifest_path, plan_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_fresh_outputs(
        (
            (manifest_path, canonical_json_bytes(manifest.to_dict()) + b"\n"),
            (plan_path, canonical_json_bytes(call_cap_plan(manifest)) + b"\n"),
        )
    )


if __name__ == "__main__":
    main()
