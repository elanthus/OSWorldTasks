"""Response-free D5.9 Haiku freeze successor with an explicit sandbox exception."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.serialization import canonical_json_bytes

SELECTION_PATH = "artifacts/grounding-v5-d59-haiku-selection/owner-selection.json"
EXCEPTION_PATH = "artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json"
CALIBRATION_PATH = "artifacts/grounding-v5-haiku-cli-replication/snapshot.json"
TASK_MANIFEST_PATH = "artifacts/grounding-v5-d59-freeze/task-manifest.json"
ADMISSION_PATH = "artifacts/grounding-v5-d59-freeze/admission.json"
HISTORICAL_PLAN_PATH = "artifacts/grounding-v5-d59-freeze/execution-plan.json"

HISTORY_POLICY_ID = "policy-79441db33362e00a1ac6"
STATELESS_POLICY_ID = "policy-e1d746cd6a83ffb12a20"
MODEL = "claude-haiku-4-5-20251001"
PROVIDER = "claude-code-cli/claude-ai-max-subscription"
MAX_MODEL_ATTEMPTS_PER_ACTION = 2
PROPOSED_RUNTIME_WINDOW_HOURS = 168
RUNTIME_MARGIN = 1.25

SOURCE_FILES = (
    "pixelgym/grounding/v5/d59_haiku_freeze.py",
    "scripts/prepare_grounding_v5_d59_haiku_freeze.py",
)


def _read(root: Path, path: str) -> dict[str, Any]:
    value = json.loads((root / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(root: Path, path: str, value: dict[str, Any]) -> dict[str, str]:
    return {
        "path": path,
        "content_digest": content_digest(value),
        "file_sha256": _sha256(root / path),
    }


def owner_exception() -> dict[str, Any]:
    value = {
        "schema_version": "pixelgym-d59-haiku-sandbox-exception-v1",
        "recorded_at": "2026-09-23",
        "owner_statement": "exception approved",
        "decision": (
            "Waive OS-level sandbox enforcement for the exact selected Haiku D5.9 "
            "policy pair and retain the calibrated Claude Code CLI controls."
        ),
        "provider_calls_made": 0,
        "scope": {
            "applies_only_to_d59_haiku_successor": True,
            "history_policy_id": HISTORY_POLICY_ID,
            "stateless_policy_id": STATELESS_POLICY_ID,
            "model": MODEL,
            "provider": PROVIDER,
            "os_sandbox_required": False,
            "os_sandbox_applied": False,
            "cli_restrictions_required": True,
            "sanitized_environment_required": True,
            "tools_required": "none",
            "mcp_required": "none",
        },
        "rationale": (
            "The matched comparison changes only screenshot history. Reusing the calibrated "
            "CLI route preserves internal comparability without introducing a new credential "
            "or provider route."
        ),
        "claim_boundary": (
            "The successor may support the matched model-quality comparison but cannot claim "
            "OS-enforced policy isolation or independent denial of local process capabilities."
        ),
        "residual_risks": [
            "CLI flags and environment filtering, rather than an OS boundary, enforce the local capability restrictions.",
            "A Claude CLI defect could exceed the declared local capability boundary.",
            "The existing macOS Keychain-backed Max credential remains available to the unsandboxed CLI process.",
        ],
        "execution_boundary": {
            "execution_enabled": False,
            "approved_model_attempt_cap": 0,
            "approved_provider_wire_request_cap": 0,
            "subscription_execution_authorized": False,
            "runtime_window_approved": False,
            "provider_calls_made": 0,
        },
        "human_gates": {
            "D5.10": "not_evaluated_human_owned",
            "public_security_claims": "not_authorized",
        },
    }
    return {**value, "exception_digest": content_digest(value)}


def _validate_selection(selection: dict[str, Any]) -> None:
    pair = selection.get("selected_pair")
    design = selection.get("selected_design")
    boundary = selection.get("execution_boundary")
    if not isinstance(pair, dict) or not isinstance(design, dict) or not isinstance(
        boundary, dict
    ):
        raise TypeError("Haiku owner selection is incomplete")
    expected_pair = {
        "cli_version": "2.1.267 (Claude Code)",
        "history_policy_id": HISTORY_POLICY_ID,
        "model": MODEL,
        "provider": PROVIDER,
        "stateless_policy_id": STATELESS_POLICY_ID,
    }
    if pair != expected_pair:
        raise ValueError("Haiku owner selection policy pair changed")
    if (
        design.get("independent_representatives") != 168
        or design.get("episodes_per_arm") != 192
        or design.get("robustness_twins_per_arm") != 24
    ):
        raise ValueError("Haiku owner selection design changed")
    if (
        boundary.get("execution_enabled") is not False
        or boundary.get("approved_model_attempt_cap") != 0
        or boundary.get("approved_provider_wire_request_cap") != 0
    ):
        raise ValueError("Haiku owner selection unexpectedly authorizes execution")


def _validate_policies(calibration: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policies = calibration.get("policy_manifests")
    if not isinstance(policies, dict) or set(policies) != {"history", "stateless"}:
        raise ValueError("Haiku calibration policy manifests are incomplete")
    expected_ids = {
        "history": HISTORY_POLICY_ID,
        "stateless": STATELESS_POLICY_ID,
    }
    for mode, policy in policies.items():
        if not isinstance(policy, dict):
            raise TypeError("Haiku policy manifest must be an object")
        if (
            policy.get("policy_id") != expected_ids[mode]
            or policy.get("model") != MODEL
            or policy.get("provider") != PROVIDER
            or policy.get("max_model_attempts_per_action")
            != MAX_MODEL_ATTEMPTS_PER_ACTION
        ):
            raise ValueError("Haiku calibration policy identity changed")
        sandbox = policy.get("sandbox")
        if not isinstance(sandbox, dict):
            raise TypeError("Haiku policy sandbox manifest must be an object")
        enforcement = sandbox.get("runtime_enforcement")
        if not isinstance(enforcement, dict) or enforcement.get("os_sandbox_applied") is not False:
            raise ValueError("Haiku exception expects the disclosed unsandboxed policy")
        inference = dict(policy.get("inference_parameters", []))
        if (
            inference.get("tools") != "none"
            or inference.get("mcp") != "none"
            or inference.get("cli_version") != "2.1.267 (Claude Code)"
        ):
            raise ValueError("Haiku CLI restriction contract changed")
    return policies


def _caps(jobs: list[dict[str, Any]]) -> dict[str, int]:
    actions = sum(int(job["action_limit"]) for job in jobs)
    attempts = actions * MAX_MODEL_ATTEMPTS_PER_ACTION
    return {
        "environment_action_cap": actions,
        "model_attempt_cap": attempts,
        "provider_control_request_cap": 0,
        "provider_wire_request_cap": attempts,
    }


def _runtime_evidence(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name in ("haiku-v4.json", "haiku-cli-continuation.json"):
        path = f"artifacts/grounding-v5-pr196-calibration/{name}"
        value = _read(root, path)
        attempts = sum(int(row.get("model_attempts", 0)) for row in value["results"])
        elapsed = float(value["accounting"]["elapsed_seconds"])
        if attempts <= 0 or elapsed <= 0:
            raise ValueError("Haiku runtime evidence is empty")
        rows.append(
            {
                "path": path,
                "file_sha256": _sha256(root / path),
                "observed_model_attempts": attempts,
                "observed_elapsed_seconds": elapsed,
                "seconds_per_attempt": elapsed / attempts,
            }
        )
    return {
        "observations": rows,
        "conservative_seconds_per_attempt": max(
            row["seconds_per_attempt"] for row in rows
        ),
        "margin_multiplier": RUNTIME_MARGIN,
    }


def execution_plan(root: Path, *, source_revision: str) -> dict[str, Any]:
    selection = _read(root, SELECTION_PATH)
    calibration = _read(root, CALIBRATION_PATH)
    task_manifest = _read(root, TASK_MANIFEST_PATH)
    admission = _read(root, ADMISSION_PATH)
    historical = _read(root, HISTORICAL_PLAN_PATH)
    exception = owner_exception()
    _validate_selection(selection)
    policies = _validate_policies(calibration)
    historical_digest = historical.get("execution_plan_digest")
    historical_body = {
        key: value for key, value in historical.items() if key != "execution_plan_digest"
    }
    if historical_digest != content_digest(historical_body):
        raise ValueError("historical D5.9 execution plan digest changed")
    task_binding = _binding(root, TASK_MANIFEST_PATH, task_manifest)
    admission_binding = _binding(root, ADMISSION_PATH, admission)
    historical_task_binding = historical.get("task_manifest_binding")
    historical_admission_binding = historical.get("admission_binding")
    if not isinstance(historical_task_binding, dict) or not isinstance(
        historical_admission_binding, dict
    ):
        raise TypeError("historical D5.9 bindings are incomplete")
    if (
        historical_task_binding.get("path") != task_binding["path"]
        or historical_task_binding.get("file_sha256") != task_binding["file_sha256"]
        or task_manifest.get("manifest_digest")
        != historical_task_binding.get("manifest_digest")
    ):
        raise ValueError("historical D5.9 task manifest binding changed")
    if (
        historical_admission_binding.get("path") != admission_binding["path"]
        or historical_admission_binding.get("file_sha256")
        != admission_binding["file_sha256"]
        or admission.get("evidence_digest")
        != historical_admission_binding.get("evidence_digest")
    ):
        raise ValueError("historical D5.9 admission binding changed")
    if historical.get("allocation") != task_manifest.get("allocation"):
        raise ValueError("historical D5.9 allocation changed")

    def successor_job(job: dict[str, Any]) -> dict[str, Any]:
        value = dict(job)
        value["trial_id"] = str(value["trial_id"]).replace(
            "d59-", "d59-haiku-", 1
        )
        return value

    primary_jobs = [successor_job(job) for job in historical["primary_jobs"]]
    reliability_jobs = [successor_job(job) for job in historical["reliability_jobs"]]
    all_jobs = primary_jobs + reliability_jobs
    phase_caps = {
        "primary": _caps(primary_jobs),
        "reliability": _caps(reliability_jobs),
        "aggregate": _caps(all_jobs),
    }
    runtime_evidence = _runtime_evidence(root)
    estimated_cap_hours = (
        phase_caps["aggregate"]["model_attempt_cap"]
        * runtime_evidence["conservative_seconds_per_attempt"]
        * RUNTIME_MARGIN
        / 3600
    )
    if PROPOSED_RUNTIME_WINDOW_HOURS < math.ceil(estimated_cap_hours):
        raise ValueError("proposed Haiku runtime window is below the derived cap estimate")
    source = {
        "schema_version": "pixelgym-d59-haiku-freeze-source-v1",
        "source_revision": source_revision,
        "files": {name: _sha256(root / name) for name in SOURCE_FILES},
    }
    source["binding_digest"] = content_digest(source)
    value = {
        "schema_version": "pixelgym-agent-v5-d59-haiku-execution-plan-v1",
        "status": "frozen_candidate_awaiting_exact_subscription_execution_approval",
        "execution_enabled": False,
        "subscription_execution_authorized": False,
        "approved_model_attempt_cap": 0,
        "approved_provider_wire_request_cap": 0,
        "provider_calls_made": 0,
        "owner_selection_binding": _binding(root, SELECTION_PATH, selection),
        "owner_exception": exception,
        "source_binding": source,
        "calibration_binding": _binding(root, CALIBRATION_PATH, calibration),
        "task_manifest_binding": task_binding,
        "admission_binding": admission_binding,
        "policy_manifests": policies,
        "allocation": historical["allocation"],
        "primary_comparison": historical["primary_comparison"],
        "security_boundary": {
            "os_sandbox_exception_approved": True,
            "os_sandbox_applied": {"history": False, "stateless": False},
            "cli_restrictions_required": True,
            "sanitized_environment_required": True,
            "prohibited_claim": "OS-enforced policy isolation",
            "exception_digest": exception["exception_digest"],
        },
        "budget": {
            "route": "claude.ai Max subscription",
            "incremental_experiment_charge_usd": "0.00",
            "aggregate_planning_cap_usd": "0.00",
            "subscription_usage_is_not_a_dollar_budget": True,
            "unused_attempt_transfer_allowed": False,
        },
        "phase_caps": phase_caps,
        "runtime": {
            "proposed_aggregate_window_hours": PROPOSED_RUNTIME_WINDOW_HOURS,
            "approval_required": True,
            "derivation": {
                **runtime_evidence,
                "mechanical_attempt_cap": phase_caps["aggregate"][
                    "model_attempt_cap"
                ],
                "estimated_cap_hours_with_margin": estimated_cap_hours,
            },
        },
        "primary_jobs": primary_jobs,
        "reliability_jobs": reliability_jobs,
        "failure_rule": (
            "retain every assigned failure and unrun assignment; no post-result tuning or "
            "replay outside the frozen reliability schedule"
        ),
        "remaining_human_boundary": {
            "approve_exact_execution_plan_digest": True,
            "approve_nonzero_attempt_caps": True,
            "approve_runtime_window": True,
            "D5_10_verdict_remains_human_owned": True,
        },
    }
    return {**value, "execution_plan_digest": content_digest(value)}


def expected_outputs(root: Path, *, source_revision: str) -> dict[str, bytes]:
    values = {
        "owner-exception.json": owner_exception(),
        "execution-plan.json": execution_plan(root, source_revision=source_revision),
    }
    return {
        name: canonical_json_bytes(value) + b"\n" for name, value in values.items()
    }
