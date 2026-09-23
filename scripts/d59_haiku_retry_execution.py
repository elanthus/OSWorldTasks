"""Exact authorization and runtime binding for the Haiku D5.9 retry successor."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest
from pixelgym.grounding.v5.d59_haiku_execution import (
    sha256_file,
    validate_assignments,
)

EXECUTION_PLAN_PATH = (
    "artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json"
)
OWNER_APPROVAL_PATH = (
    "artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json"
)
EXECUTION_PLAN_DIGEST = (
    "sha256:c123aad69824e2ec352fd1751fafd762e43a0f2697cdd00e4521efe6509d5cf7"
)
APPROVED_RUNTIME_HOURS = 168
APPROVED_CAPS = CallCaps(12134, 24268, 0, 24268)
EXECUTION_SOURCE_FILES = (
    "scripts/d59_haiku_retry_execution.py",
    "scripts/run_grounding_v5_d59_haiku.py",
    "scripts/run_grounding_v5_d59_haiku_retry_successor.py",
)


def expected_owner_approval(plan: dict[str, Any]) -> dict[str, Any]:
    """Return the exact structured record authorized by the owner's 2026-09-23 yes."""

    exception = plan["security_boundary"]
    return {
        "schema_version": "pixelgym-agent-v5-d59-haiku-execution-approval-v2",
        "recorded_at": "2026-09-23",
        "owner_statement": "yes",
        "execution_plan_digest": EXECUTION_PLAN_DIGEST,
        "approved_caps": APPROVED_CAPS.to_dict(),
        "approved_runtime_window_hours": APPROVED_RUNTIME_HOURS,
        "subscription_execution_authorized": True,
        "incremental_experiment_charge_cap_usd": "0.00",
        "provider_calls_made_at_authorization": 0,
        "scope": {
            "model": "claude-haiku-4-5-20251001",
            "provider": "claude-code-cli/claude-ai-max-subscription",
            "history_policy_id": "policy-6788af9cc24b21e0b89f",
            "stateless_policy_id": "policy-9ce79eec1338f6426873",
            "sandbox_exception_digest": exception["exception_digest"],
        },
        "remaining_human_boundary": {
            "D5_10_verdict_remains_human_owned": True,
            "public_model_quality_claims_authorized": False,
            "public_security_claims_authorized": False,
        },
    }


def validate_execution_authorization(
    plan: dict[str, Any], approval: dict[str, Any]
) -> None:
    body = {key: value for key, value in plan.items() if key != "execution_plan_digest"}
    if plan.get("execution_plan_digest") != content_digest(body):
        raise ValueError("frozen Haiku D5.9 successor-plan digest is invalid")
    if plan["execution_plan_digest"] != EXECUTION_PLAN_DIGEST:
        raise ValueError("Haiku D5.9 successor-plan digest differs from owner approval")
    if plan.get("execution_enabled") is not False:
        raise ValueError("frozen successor must remain immutable and non-executable")
    if plan.get("subscription_execution_authorized") is not False:
        raise ValueError("frozen successor must remain unauthorized")
    if plan.get("approved_model_attempt_cap") != 0:
        raise ValueError("frozen successor model-attempt approval must remain zero")
    if plan.get("approved_provider_wire_request_cap") != 0:
        raise ValueError("frozen successor wire-request approval must remain zero")
    if plan.get("phase_caps", {}).get("aggregate") != APPROVED_CAPS.to_dict():
        raise ValueError("frozen aggregate caps differ from owner approval")
    if plan.get("runtime", {}).get("proposed_aggregate_window_hours") != APPROVED_RUNTIME_HOURS:
        raise ValueError("frozen runtime window differs from owner approval")
    if approval != expected_owner_approval(plan):
        raise ValueError("execution authorization differs from the exact approved successor")


def validated_live_manifests(
    root: Path,
    plan: dict[str, Any],
    runtime_identity: claude.ClaudeRuntimeIdentity,
) -> dict[str, PolicyManifest]:
    """Rebuild the zero-retry manifests and require their exact frozen identity."""

    frozen = plan.get("policy_manifests")
    if not isinstance(frozen, dict) or set(frozen) != {"history", "stateless"}:
        raise ValueError("frozen Haiku successor policy manifests are incomplete")
    code_revisions = {str(value["code_revision"]) for value in frozen.values()}
    if len(code_revisions) != 1:
        raise ValueError("frozen Haiku successor policies do not share one revision")
    base = claude.build_claude_policy_manifest(
        root,
        code_revision=code_revisions.pop(),
        runtime_identity=runtime_identity,
        resolved_model=claude.MODEL,
        api_retry_limit=0,
    )
    live = {
        mode: build_memory_manifest(root, base, retain_screenshots=mode == "history")
        for mode in ("history", "stateless")
    }
    for mode, manifest in live.items():
        if manifest.to_dict() != frozen[mode]:
            raise ValueError(
                f"live {mode} policy differs from the frozen successor manifest"
            )
    return live


def execution_binding(
    root: Path,
    *,
    plan: dict[str, Any],
    approval: dict[str, Any],
    runtime_identity: claude.ClaudeRuntimeIdentity,
) -> dict[str, Any]:
    return {
        "schema_version": "pixelgym-agent-v5-d59-haiku-execution-binding-v2",
        "execution_source": {
            "revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "files": {
                path: sha256_file(root / path) for path in EXECUTION_SOURCE_FILES
            },
        },
        "execution_plan": {
            "path": EXECUTION_PLAN_PATH,
            "content_digest": content_digest(plan),
            "file_sha256": sha256_file(root / EXECUTION_PLAN_PATH),
            "execution_plan_digest": EXECUTION_PLAN_DIGEST,
        },
        "owner_approval": {
            "path": OWNER_APPROVAL_PATH,
            "content_digest": content_digest(approval),
            "file_sha256": sha256_file(root / OWNER_APPROVAL_PATH),
        },
        "runtime_identity": runtime_identity.to_dict(),
        "approved_caps": APPROVED_CAPS.to_dict(),
        "approved_runtime_window_hours": APPROVED_RUNTIME_HOURS,
        "provider_calls_made_during_binding": 0,
    }
