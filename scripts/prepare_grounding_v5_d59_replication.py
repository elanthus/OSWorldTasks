"""Prepare or verify the no-call D5.9 replication freeze candidate.

Without ``--verify`` the installed CLI is bound to the replication route with
``claude --version`` and ``claude --help`` only; no login and no provider call. With
``--verify`` the committed candidate is rebuilt from its own recorded identity and compared
byte for byte.
"""

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.d59_replication import OUTPUT_DIRECTORY, execution_plan
from pixelgym.serialization import canonical_json_bytes
from scripts.prepare_grounding_v5_d59_haiku_freeze import write_outputs

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    public = ROOT / OUTPUT_DIRECTORY
    if args.verify:
        stored = json.loads((public / "execution-plan.json").read_text(encoding="utf-8"))
        identity = claude.ClaudeRuntimeIdentity(**stored["runtime_identity"])
    else:
        identity = claude.probe_claude_route(claude.REPLICATION_ROUTE)
    plan = execution_plan(ROOT, source_revision=args.source_revision, runtime_identity=identity)
    write_outputs(
        {"execution-plan.json": canonical_json_bytes(plan) + b"\n"},
        verify=args.verify,
        public=public,
    )
    print(
        json.dumps(
            {
                "execution_plan_digest": plan["execution_plan_digest"],
                "history_policy_id": plan["policy_manifests"]["history"]["policy_id"],
                "stateless_policy_id": plan["policy_manifests"]["stateless"]["policy_id"],
                "pilot_assignments": len(plan["pilot"]["jobs"]),
                "full_assignments": len(plan["primary_jobs"]) + len(plan["reliability_jobs"]),
                "execution_enabled": plan["execution_enabled"],
                "provider_calls_made": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
