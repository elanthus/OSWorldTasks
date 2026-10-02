"""Prepare or execute the exactly approved Haiku D5.9 retry successor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.contracts import PolicyManifest
from scripts import d59_haiku_retry_execution as authorization
from scripts import run_grounding_v5_d59_haiku as runner


def validated_transport_manifests(
    root: Path, plan: dict[str, Any], runtime_identity: claude.ClaudeRuntimeIdentity
) -> dict[str, PolicyManifest]:
    """Reject transport configuration drift before the shared runner opens journals."""

    manifests = authorization.validated_live_manifests(root, plan, runtime_identity)
    limits = {
        dict(manifest.inference_parameters).get("cli_api_retry_limit")
        for manifest in manifests.values()
    }
    expected = None if runner.CLI_API_RETRY_LIMIT is None else str(runner.CLI_API_RETRY_LIMIT)
    if limits != {expected}:
        raise ValueError("CLI API retry limit differs from the validated policy manifests")
    return manifests


def main() -> None:
    """Run the shared fail-closed executor with the successor authorization contract."""

    runner.APPROVED_CAPS = authorization.APPROVED_CAPS
    runner.APPROVED_RUNTIME_HOURS = authorization.APPROVED_RUNTIME_HOURS
    runner.EXECUTION_PLAN_DIGEST = authorization.EXECUTION_PLAN_DIGEST
    runner.EXECUTION_PLAN_PATH = authorization.EXECUTION_PLAN_PATH
    runner.OWNER_APPROVAL_PATH = authorization.OWNER_APPROVAL_PATH
    runner.CLI_API_RETRY_LIMIT = 0
    runner.execution_binding = authorization.execution_binding
    runner.validate_assignments = authorization.validate_assignments
    runner.validate_execution_authorization = authorization.validate_execution_authorization
    runner.validated_live_manifests = validated_transport_manifests
    runner.main()


if __name__ == "__main__":
    main()
