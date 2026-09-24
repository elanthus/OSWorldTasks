"""Execute a separately approved D5.9 plan with counted connection-reset retries."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from pixelgym.grounding.v5.d59_haiku_network_retry import (
    OUTPUT_DIRECTORY,
    SOURCE_FILES,
    execution_plan,
    live_manifests,
    validate_approval,
)
from scripts import run_grounding_v5_d59_haiku as runner

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = OUTPUT_DIRECTORY + "/execution-plan.json"


def validate_plan(plan: dict[str, Any], approval: dict[str, Any]) -> None:
    expected = execution_plan(ROOT, source_revision=plan["source_binding"]["source_revision"])
    if plan != expected:
        raise ValueError("network-retry plan differs from the committed candidate")
    validate_approval(plan, approval)


def manifests(
    root: Path, plan: dict[str, Any], identity: claude.ClaudeRuntimeIdentity
) -> dict[str, Any]:
    result = live_manifests(
        root, source_revision=plan["source_binding"]["source_revision"], identity=identity
    )
    if {key: value.to_dict() for key, value in result.items()} != plan["policy_manifests"]:
        raise ValueError("live policies differ from the network-retry plan")
    return result


def binding(
    root: Path,
    *,
    plan: dict[str, Any],
    approval: dict[str, Any],
    runtime_identity: claude.ClaudeRuntimeIdentity,
) -> dict[str, Any]:
    return {
        "schema_version": "pixelgym-agent-v5-d59-haiku-network-retry-binding-v1",
        "execution_plan_digest": content_digest(plan),
        "owner_approval_digest": content_digest(approval),
        "runtime_identity": runtime_identity.to_dict(),
        "source_files": {path: sha256_file(root / path) for path in SOURCE_FILES},
        "provider_calls_made_during_binding": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    args = parser.parse_args()
    plan = runner.read_object(ROOT / PLAN_PATH)
    runner.EXECUTION_PLAN_PATH = PLAN_PATH
    runner.EXECUTION_PLAN_DIGEST = plan["execution_plan_digest"]
    runner.OWNER_APPROVAL_PATH = str(args.approval.resolve())
    runner.validate_execution_authorization = validate_plan
    runner.validated_live_manifests = manifests
    runner.execution_binding = binding
    runner.CLI_API_RETRY_LIMIT = 0
    runner.ALLOW_CONNECTION_RETRY = True
    if args.mode == "prepare":
        runner.prepare(args.output.resolve())
    else:
        runner.execute(args.output.resolve())


if __name__ == "__main__":
    main()
