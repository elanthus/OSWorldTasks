"""No-call D5.9 successor for one counted timeout or connection-reset retry."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest
from pixelgym.grounding.v5.d59_haiku_retry_successor import (
    CALIBRATION_PATH,
    _binding,
    _read,
    _sha256_at_revision,
    _validate_policy_inputs,
)

PREDECESSOR_PATH = "artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json"
PREDECESSOR_DIGEST = "sha256:c123aad69824e2ec352fd1751fafd762e43a0f2697cdd00e4521efe6509d5cf7"
OUTPUT_DIRECTORY = "artifacts/grounding-v5-d59-haiku-network-retry"
SOURCE_FILES = (
    "pixelgym/grounding/v5/d59_haiku_network_retry.py",
    "scripts/prepare_grounding_v5_d59_haiku_network_retry.py",
    "scripts/run_grounding_v5_d59_haiku_network_retry.py",
    "scripts/run_grounding_v5_d59_haiku.py",
)


def live_manifests(
    root: Path, *, source_revision: str, identity: claude.ClaudeRuntimeIdentity
) -> dict[str, PolicyManifest]:
    base = claude.build_claude_policy_manifest(
        root,
        code_revision=source_revision,
        runtime_identity=identity,
        resolved_model=claude.MODEL,
        api_retry_limit=0,
    )
    return {
        mode: build_memory_manifest(
            root, base, retain_screenshots=mode == "history", allow_connection_retry=True
        )
        for mode in ("history", "stateless")
    }


def execution_plan(root: Path, *, source_revision: str) -> dict[str, Any]:
    predecessor = _read(root, PREDECESSOR_PATH)
    body = {k: v for k, v in predecessor.items() if k != "execution_plan_digest"}
    if (
        content_digest(body) != PREDECESSOR_DIGEST
        or predecessor.get("execution_plan_digest") != PREDECESSOR_DIGEST
    ):
        raise ValueError("network-retry predecessor changed")
    _validate_policy_inputs(root, source_revision)
    source = {path: _sha256_at_revision(root, source_revision, path) for path in SOURCE_FILES}
    # Require the execution entry points, not only package sources, to match the commit.

    for path, digest in source.items():
        if "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"execution source differs from revision: {path}")
    identity = claude.ClaudeRuntimeIdentity(**_read(root, CALIBRATION_PATH)["runtime_identity"])
    value = deepcopy(body)
    value.update(
        schema_version="pixelgym-agent-v5-d59-haiku-network-retry-plan-v1",
        owner_direction={
            "recorded_at": "2026-09-23",
            "statement": "I would like retries to be allowed, these are just intermittent network issues. Only count errors as failures.",
            "interpretation": "One counted retry for a stopped timeout or connection reset. A recovered error is not an episode failure; an unrecovered error is retained.",
        },
        predecessor={
            "execution_plan": _binding(root, PREDECESSOR_PATH, predecessor),
            "outcomes_reused": 0,
            "assignments_replayed": 0,
            "disposition": "prior_campaigns_preserved_without_reclassification",
        },
        source_binding={"source_revision": source_revision, "files": source},
        policy_manifests={
            k: v.to_dict()
            for k, v in live_manifests(
                root, source_revision=source_revision, identity=identity
            ).items()
        },
        change_control={
            "changed_behavior": "Allow one runner-counted connection-reset retry in the existing timeout retry budget.",
            "cli_api_retry_limit": 0,
            "max_bounded_retries_per_action": 1,
            "recovered_error_is_episode_failure": False,
            "retry_same_request_and_checkpoint": True,
            "every_attempt_and_wire_request_counted": True,
            "permanent_errors_and_invalid_actions_retried": False,
        },
    )
    for phase in ("primary", "reliability"):
        for job in value[f"{phase}_jobs"]:
            job["trial_id"] = job["trial_id"].replace("d59-haiku-r2-", "d59-haiku-r3-", 1)
    return {**value, "execution_plan_digest": content_digest(value)}


def validate_approval(plan: dict[str, Any], approval: dict[str, Any]) -> None:
    """Bind execution to a separately recorded decision for this exact new plan."""
    body = {key: value for key, value in plan.items() if key != "execution_plan_digest"}
    if plan.get("execution_plan_digest") != content_digest(body):
        raise ValueError("network-retry plan digest is invalid")
    if approval != {
        "execution_plan_digest": plan["execution_plan_digest"],
        "approved_caps": plan["phase_caps"]["aggregate"],
        "approved_runtime_window_hours": plan["runtime"]["proposed_aggregate_window_hours"],
        "subscription_execution_authorized": True,
        "incremental_experiment_charge_cap_usd": "0.00",
    }:
        raise ValueError("approval must bind the exact network-retry plan, caps, and runtime")
