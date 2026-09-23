"""Exact authorization and runtime validation for the Haiku D5.9 execution."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import build_memory_manifest
from pixelgym.grounding.v5.contracts import CallCaps, PolicyManifest, content_digest
from pixelgym.grounding.v5.memory_generator import generate_memory_task

EXECUTION_PLAN_PATH = "artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json"
OWNER_APPROVAL_PATH = "artifacts/grounding-v5-d59-haiku-execution/owner-approval.json"
EXECUTION_PLAN_DIGEST = (
    "sha256:9523674b3cc8b9bca053072103ec885802ce5eec6109ca94fc843fa792c10a52"
)
APPROVED_RUNTIME_HOURS = 168
APPROVED_CAPS = CallCaps(12134, 24268, 0, 24268)
EXECUTION_SOURCE_FILES = (
    "pixelgym/grounding/v5/d59_haiku_execution.py",
    "scripts/run_grounding_v5_d59_haiku.py",
)


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def expected_owner_approval(plan: dict[str, Any]) -> dict[str, Any]:
    """Return the exact structured record authorized by the owner's 2026-09-23 yes."""

    exception = plan["owner_exception"]
    return {
        "schema_version": "pixelgym-agent-v5-d59-haiku-execution-approval-v1",
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
            "history_policy_id": "policy-79441db33362e00a1ac6",
            "stateless_policy_id": "policy-e1d746cd6a83ffb12a20",
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
        raise ValueError("frozen Haiku D5.9 execution-plan digest is invalid")
    if plan["execution_plan_digest"] != EXECUTION_PLAN_DIGEST:
        raise ValueError("Haiku D5.9 execution-plan digest differs from owner approval")
    if plan.get("execution_enabled") is not False:
        raise ValueError("frozen candidate must remain immutable and non-executable")
    if plan.get("phase_caps", {}).get("aggregate") != APPROVED_CAPS.to_dict():
        raise ValueError("frozen aggregate caps differ from owner approval")
    if plan.get("runtime", {}).get("proposed_aggregate_window_hours") != APPROVED_RUNTIME_HOURS:
        raise ValueError("frozen runtime window differs from owner approval")
    if approval != expected_owner_approval(plan):
        raise ValueError("execution authorization differs from the exact approved plan")


def validate_assignments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for phase in ("primary", "reliability"):
        phase_jobs = plan.get(f"{phase}_jobs")
        if not isinstance(phase_jobs, list):
            raise TypeError(f"{phase} jobs must be a list")
        calculated = CallCaps.calculate(
            max_episode_steps=tuple(int(job["action_limit"]) for job in phase_jobs),
            max_model_attempts_per_action=2,
            max_cancellation_requests_per_attempt=0,
            max_reconciliation_requests_per_attempt=0,
        )
        if calculated.to_dict() != plan["phase_caps"][phase]:
            raise ValueError(f"{phase} cap does not match its frozen assignments")
        for source in phase_jobs:
            job = dict(source)
            if job.get("phase") != phase:
                raise ValueError("assignment phase differs from its frozen phase list")
            task = generate_memory_task(int(job["seed"]))
            if (
                task.task_id != job.get("task_id")
                or content_digest(task.canonical_dict()) != job.get("task_digest")
                or task.max_episode_steps != job.get("action_limit")
                or task.seed_record.to_dict() != job.get("seed_record")
            ):
                raise ValueError("frozen Haiku D5.9 task assignment changed")
            jobs.append(job)
    if len({job["trial_id"] for job in jobs}) != len(jobs):
        raise ValueError("frozen Haiku D5.9 trial IDs are not unique")
    aggregate = CallCaps.calculate(
        max_episode_steps=tuple(int(job["action_limit"]) for job in jobs),
        max_model_attempts_per_action=2,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
    )
    if aggregate != APPROVED_CAPS:
        raise ValueError("frozen assignments do not reproduce the approved aggregate caps")
    return jobs


def validated_live_manifests(
    root: Path,
    plan: dict[str, Any],
    runtime_identity: claude.ClaudeRuntimeIdentity,
) -> dict[str, PolicyManifest]:
    """Rebuild the executable manifests and require exact calibration identity."""

    frozen = plan.get("policy_manifests")
    if not isinstance(frozen, dict) or set(frozen) != {"history", "stateless"}:
        raise ValueError("frozen Haiku policy manifests are incomplete")
    code_revisions = {str(value["code_revision"]) for value in frozen.values()}
    if len(code_revisions) != 1:
        raise ValueError("frozen Haiku policies do not share one code revision")
    base = claude.build_claude_policy_manifest(
        root,
        code_revision=code_revisions.pop(),
        runtime_identity=runtime_identity,
        resolved_model=claude.MODEL,
    )
    live = {
        mode: build_memory_manifest(root, base, retain_screenshots=mode == "history")
        for mode in ("history", "stateless")
    }
    for mode, manifest in live.items():
        if manifest.to_dict() != frozen[mode]:
            raise ValueError(f"live {mode} policy differs from the frozen manifest")
    return live


def execution_binding(
    root: Path,
    *,
    plan: dict[str, Any],
    approval: dict[str, Any],
    runtime_identity: claude.ClaudeRuntimeIdentity,
) -> dict[str, Any]:
    return {
        "schema_version": "pixelgym-agent-v5-d59-haiku-execution-binding-v1",
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
