"""Prepare/verify a separate, disabled D5.9 parser-sensitivity candidate without CLI calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.claude_code_policy import ClaudeRuntimeIdentity
from pixelgym.grounding.v5.d59_extraction_successor import (
    ORIGINAL_PLAN_PATH,
    OUTPUT_DIRECTORY,
    changed_fields,
    execution_plan,
)
from pixelgym.serialization import canonical_json_bytes
from scripts.prepare_grounding_v5_d59_haiku_freeze import write_outputs

ROOT = Path(__file__).resolve().parents[1]


def outputs(root: Path, revision: str) -> dict[str, bytes]:
    original = json.loads((root / ORIGINAL_PLAN_PATH).read_text())
    plan = execution_plan(
        root,
        source_revision=revision,
        runtime_identity=ClaudeRuntimeIdentity(**original["runtime_identity"]),
    )
    changes = {
        "original_plan_digest": original["execution_plan_digest"],
        "successor_plan_digest": plan["execution_plan_digest"],
        "changed_fields": changed_fields(original, plan),
        "owner_approval_recorded": False,
        "provider_calls": 0,
    }
    return {
        "execution-plan.json": canonical_json_bytes(plan) + b"\n",
        "plan-field-changes.json": canonical_json_bytes(changes) + b"\n",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    prepared = outputs(ROOT, args.source_revision)
    write_outputs(prepared, public=ROOT / OUTPUT_DIRECTORY, verify=args.verify)
    plan = json.loads(prepared["execution-plan.json"])
    print(
        json.dumps(
            {
                "execution_plan_digest": plan["execution_plan_digest"],
                "policy_ids": {
                    mode: m["policy_id"] for mode, m in plan["policy_manifests"].items()
                },
                "execution_enabled": plan["execution_enabled"],
                "approved_model_attempt_cap": plan["approved_model_attempt_cap"],
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
