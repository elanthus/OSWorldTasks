"""Print a no-provider-call v5 cap plan for a checked-in policy manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.contracts import PolicyManifest, SandboxManifest
from pixelgym.grounding.v5.planning import call_cap_plan
from pixelgym.serialization import canonical_json_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    sandbox = SandboxManifest(**value.pop("sandbox"))
    value["inference_parameters"] = tuple(
        tuple(item) for item in value.get("inference_parameters", [])
    )
    manifest = PolicyManifest(sandbox=sandbox, **value)
    print(canonical_json_text(call_cap_plan(manifest)))


if __name__ == "__main__":
    main()
