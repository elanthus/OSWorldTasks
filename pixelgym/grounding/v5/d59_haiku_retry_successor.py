"""Response-free D5.9 successor with Claude CLI API retries disabled."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.serialization import canonical_json_bytes

PREDECESSOR_PLAN_PATH = "artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json"
PREDECESSOR_APPROVAL_PATH = "artifacts/grounding-v5-d59-haiku-execution/owner-approval.json"
DISCARDED_RUN_PATH = "artifacts/grounding-v5-d59-haiku-api-retry-successor/discarded-run.json"
CALIBRATION_PATH = "artifacts/grounding-v5-haiku-cli-replication/snapshot.json"
OUTPUT_DIRECTORY = "artifacts/grounding-v5-d59-haiku-api-retry-successor"
PREDECESSOR_PLAN_DIGEST = "sha256:9523674b3cc8b9bca053072103ec885802ce5eec6109ca94fc843fa792c10a52"
API_RETRY_LIMIT = 0

SOURCE_FILES = (
    "pixelgym/grounding/v5/claude_code_policy.py",
    "pixelgym/grounding/v5/d59_haiku_retry_successor.py",
    "scripts/prepare_grounding_v5_d59_haiku_retry_successor.py",
)


def _read(root: Path, path: str) -> dict[str, Any]:
    value = json.loads((root / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_at_revision(root: Path, revision: str, path: str) -> str:
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise ValueError("source revision must be a full lowercase Git commit SHA")
    try:
        payload = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise ValueError(f"source file is unavailable at revision: {path}") from error
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _binding(root: Path, path: str, value: dict[str, Any]) -> dict[str, str]:
    return {
        "path": path,
        "content_digest": content_digest(value),
        "file_sha256": _sha256(root / path),
    }


def _prospective_binding(path: str, value: dict[str, Any]) -> dict[str, str]:
    payload = canonical_json_bytes(value) + b"\n"
    return {
        "path": path,
        "content_digest": content_digest(value),
        "file_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }


def _validate_predecessor(
    predecessor: dict[str, Any], approval: dict[str, Any], discarded: dict[str, Any]
) -> None:
    body = {key: value for key, value in predecessor.items() if key != "execution_plan_digest"}
    if predecessor.get("execution_plan_digest") != content_digest(body):
        raise ValueError("predecessor execution-plan digest is invalid")
    if predecessor["execution_plan_digest"] != PREDECESSOR_PLAN_DIGEST:
        raise ValueError("predecessor execution-plan digest changed")
    if approval.get("execution_plan_digest") != PREDECESSOR_PLAN_DIGEST:
        raise ValueError("predecessor owner approval changed")
    if approval.get("subscription_execution_authorized") is not True:
        raise ValueError("predecessor execution was not authorized")
    if discarded.get("execution_plan_digest") != PREDECESSOR_PLAN_DIGEST:
        raise ValueError("discarded-run receipt does not bind the predecessor plan")
    if (
        discarded.get("disposition") != "discarded_from_scoring"
        or discarded.get("restart_or_replay_performed") is not False
        or discarded.get("provider_response_content_retained") is not False
    ):
        raise ValueError("discarded-run receipt does not preserve the failure boundary")
    observed = discarded.get("observed_usage")
    if not isinstance(observed, dict):
        raise TypeError("discarded-run observed usage must be an object")
    if (
        observed.get("runner_model_attempts") != 18
        or observed.get("runner_provider_wire_requests") != 18
        or observed.get("cli_internal_api_retry_events") != 2
        or observed.get("minimum_provider_api_attempts") != 20
    ):
        raise ValueError("discarded-run usage accounting changed")
    violation = discarded.get("policy_violation")
    if not isinstance(violation, dict) or violation.get("code") != (
        "unauthorized_system_event:api_retry"
    ):
        raise ValueError("discarded-run policy violation changed")


def _successor_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for source in jobs:
        value = dict(source)
        trial = str(value["trial_id"])
        if not trial.startswith("d59-haiku-"):
            raise ValueError("predecessor trial ID does not use the Haiku namespace")
        value["trial_id"] = trial.replace("d59-haiku-", "d59-haiku-r2-", 1)
        values.append(value)
    if len({value["trial_id"] for value in values}) != len(values):
        raise ValueError("successor trial IDs are not unique")
    return values


def execution_plan(
    root: Path, *, source_revision: str, discarded_run: dict[str, Any]
) -> dict[str, Any]:
    predecessor = _read(root, PREDECESSOR_PLAN_PATH)
    approval = _read(root, PREDECESSOR_APPROVAL_PATH)
    calibration = _read(root, CALIBRATION_PATH)
    _validate_predecessor(predecessor, approval, discarded_run)
    identity = claude.ClaudeRuntimeIdentity(**calibration["runtime_identity"])
    base = claude.build_claude_policy_manifest(
        root,
        code_revision=source_revision,
        runtime_identity=identity,
        resolved_model=claude.MODEL,
        api_retry_limit=API_RETRY_LIMIT,
    )
    policies = {
        mode: build_memory_manifest(root, base, retain_screenshots=mode == "history").to_dict()
        for mode in ("history", "stateless")
    }
    predecessor_policy_ids = {
        value["policy_id"] for value in predecessor["policy_manifests"].values()
    }
    if any(value["policy_id"] in predecessor_policy_ids for value in policies.values()):
        raise ValueError("successor policy identity did not change")
    for value in policies.values():
        inference = dict(value["inference_parameters"])
        if (
            inference.get("cli_api_retry_limit") != "0"
            or inference.get("cli_api_retry_environment_variable")
            != claude.CLI_API_RETRY_ENVIRONMENT_VARIABLE
        ):
            raise ValueError("successor policy does not bind zero Claude CLI API retries")

    source = {
        "schema_version": "pixelgym-d59-haiku-api-retry-successor-source-v1",
        "source_revision": source_revision,
        "files": {path: _sha256_at_revision(root, source_revision, path) for path in SOURCE_FILES},
    }
    source["binding_digest"] = content_digest(source)
    value = {
        "schema_version": "pixelgym-agent-v5-d59-haiku-api-retry-successor-plan-v1",
        "status": "frozen_candidate_awaiting_exact_subscription_execution_approval",
        "execution_enabled": False,
        "subscription_execution_authorized": False,
        "approved_model_attempt_cap": 0,
        "approved_provider_wire_request_cap": 0,
        "provider_calls_made_during_successor_preparation": 0,
        "owner_direction": {
            "recorded_at": "2026-09-23",
            "statement": (
                "Discard the stopped run from scoring and prepare a versioned successor "
                "with a bugfix for the unauthorized event."
            ),
            "destructive_deletion_performed": False,
        },
        "predecessor": {
            "execution_plan": _binding(root, PREDECESSOR_PLAN_PATH, predecessor),
            "owner_approval": _binding(root, PREDECESSOR_APPROVAL_PATH, approval),
            "discarded_run": _prospective_binding(DISCARDED_RUN_PATH, discarded_run),
            "outcomes_reused": 0,
            "assignments_replayed": 0,
            "disposition": "retained_as_invalid_infrastructure_evidence_only",
        },
        "source_binding": source,
        "runtime_identity": identity.to_dict(),
        "policy_manifests": policies,
        "change_control": {
            "changed_behavior": (
                "Set CLAUDE_CODE_MAX_RETRIES=0 in the sanitized Claude child environment."
            ),
            "unchanged_behavior": [
                "The parser continues to classify every api_retry system event as a policy violation.",
                "The model, prompt, action parser, task manifest, admission, history reducer, runner retry, reliability schedule, caps, and sandbox exception are unchanged.",
                "Successful one-request Claude responses follow the same request and action contracts.",
            ],
            "reason": (
                "The predecessor exposed two hidden Claude CLI API retries inside one runner "
                "attempt, so accepting api_retry telemetry would undercount provider requests."
            ),
            "cli_api_retry_limit": API_RETRY_LIMIT,
            "fail_closed_if_api_retry_observed": True,
        },
        "security_boundary": predecessor["security_boundary"],
        "budget": predecessor["budget"],
        "allocation": predecessor["allocation"],
        "primary_comparison": predecessor["primary_comparison"],
        "phase_caps": predecessor["phase_caps"],
        "runtime": predecessor["runtime"],
        "primary_jobs": _successor_jobs(predecessor["primary_jobs"]),
        "reliability_jobs": _successor_jobs(predecessor["reliability_jobs"]),
        "failure_rule": predecessor["failure_rule"],
        "remaining_human_boundary": {
            "approve_exact_successor_plan_digest": True,
            "approve_nonzero_attempt_caps": True,
            "approve_runtime_window": True,
            "D5_10_verdict_remains_human_owned": True,
        },
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def expected_outputs(
    root: Path, *, source_revision: str, discarded_run: dict[str, Any]
) -> dict[str, bytes]:
    plan = execution_plan(root, source_revision=source_revision, discarded_run=discarded_run)
    return {
        "discarded-run.json": canonical_json_bytes(discarded_run) + b"\n",
        "execution-plan.json": canonical_json_bytes(plan) + b"\n",
    }
