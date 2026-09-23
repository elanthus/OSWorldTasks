"""Prepare or execute the exactly approved Haiku D5.9 confirmatory campaign."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy, CliMemoryRunner
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import (
    APPROVED_CAPS,
    APPROVED_RUNTIME_HOURS,
    EXECUTION_PLAN_DIGEST,
    EXECUTION_PLAN_PATH,
    OWNER_APPROVAL_PATH,
    execution_binding,
    validate_assignments,
    validate_execution_authorization,
    validated_live_manifests,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.screenshot_memory import require_clean_tracked_worktree
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_BENCHMARK_OUTCOMES = {"success_termination", "step_limit_truncation"}
SUMMARY_SCHEMA_VERSION = "pixelgym-agent-v5-d59-haiku-execution-summary-v1"
CLI_API_RETRY_LIMIT: int | None = None


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    temporary.replace(path)


def prepared_inputs() -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    claude.ClaudeRuntimeIdentity,
    dict[str, Any],
]:
    require_clean_tracked_worktree(ROOT)
    plan = read_object(ROOT / EXECUTION_PLAN_PATH)
    approval = read_object(ROOT / OWNER_APPROVAL_PATH)
    validate_execution_authorization(plan, approval)
    jobs = validate_assignments(plan)
    identity = claude.probe_claude_runtime()
    manifests = validated_live_manifests(ROOT, plan, identity)
    binding = execution_binding(
        ROOT,
        plan=plan,
        approval=approval,
        runtime_identity=identity,
    )
    binding["policy_manifest_digests"] = {
        mode: content_digest(manifest.to_dict()) for mode, manifest in manifests.items()
    }
    return plan, approval, jobs, identity, binding


def prepare(output: Path) -> None:
    _plan, _approval, jobs, _identity, binding = prepared_inputs()
    output.mkdir(parents=True, exist_ok=False)
    (output / "execution-plan.json").write_bytes(
        (ROOT / EXECUTION_PLAN_PATH).read_bytes()
    )
    (output / "owner-approval.json").write_bytes(
        (ROOT / OWNER_APPROVAL_PATH).read_bytes()
    )
    write_object(output / "execution-binding.json", binding)
    print(
        json.dumps(
            {
                "execution_plan_digest": EXECUTION_PLAN_DIGEST,
                "assignments": len(jobs),
                "conditions": dict(Counter(str(job["mode"]) for job in jobs)),
                "phases": dict(Counter(str(job["phase"]) for job in jobs)),
                "approved_caps": APPROVED_CAPS.to_dict(),
                "approved_runtime_window_hours": APPROVED_RUNTIME_HOURS,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


def execute(output: Path) -> None:
    plan, approval, jobs, identity, binding = prepared_inputs()
    if read_object(output / "execution-plan.json") != plan:
        raise ValueError("prepared execution plan differs from the approved plan")
    if read_object(output / "owner-approval.json") != approval:
        raise ValueError("prepared owner approval differs from the approved record")
    if read_object(output / "execution-binding.json") != binding:
        raise ValueError("prepared runtime binding differs from the live runtime")
    if (output / "attempts.sqlite").exists() or (output / "invocations.sqlite").exists():
        raise FileExistsError("never restart or overwrite an existing D5.9 campaign")

    manifests = validated_live_manifests(ROOT, plan, identity)
    ledger = SubscriptionExemptLedger(Decimal("1.00"), Decimal("0.00"))
    invocations = claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    transport = claude.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocations,
        runtime_identity=identity,
        expected_resolved_model=claude.MODEL,
        allow_timeout_retry=True,
        api_retry_limit=CLI_API_RETRY_LIMIT,
    )
    journal = V5AttemptJournal(output / "attempts.sqlite")
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    maximum_elapsed_seconds = APPROVED_RUNTIME_HOURS * 3600
    stop_reason = "running"
    error: dict[str, str] | None = None

    def attempted_assignments() -> int:
        return sum(
            event.kind == "d59_assignment_started" for event in journal.events()
        )

    def phase_usage(phase: str) -> dict[str, int]:
        selected = [row for row in rows if row["phase"] == phase]
        return {
            "environment_actions": sum(
                int(row["environment_actions"]) for row in selected
            ),
            "model_attempts": sum(int(row["model_attempts"]) for row in selected),
            "provider_control_requests": sum(
                int(row["provider_control_requests"]) for row in selected
            ),
            "provider_wire_requests": sum(
                int(row["provider_wire_requests"]) for row in selected
            ),
        }

    def summary() -> dict[str, Any]:
        elapsed = round(time.monotonic() - started, 3)
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "execution_plan_digest": EXECUTION_PLAN_DIGEST,
            "execution_binding_digest": content_digest(binding),
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "model": claude.MODEL,
            "provider": claude.PROVIDER_IDENTITY,
            "assigned": len(jobs),
            "attempted": attempted_assignments(),
            "completed": len(rows),
            "unrun": len(jobs) - attempted_assignments(),
            "stop_reason": stop_reason,
            "error": error,
            "elapsed_seconds": elapsed,
            "approved_runtime_seconds": maximum_elapsed_seconds,
            "results": rows,
            "counts": {
                phase: {
                    mode: dict(
                        Counter(
                            row["classification"]
                            for row in rows
                            if row["phase"] == phase and row["mode"] == mode
                        )
                    )
                    for mode in ("history", "stateless")
                }
                for phase in ("primary", "reliability")
            },
            "phase_usage": {
                phase: phase_usage(phase) for phase in ("primary", "reliability")
            },
            "provider_processes_started": ledger.processes_started,
            "incremental_experiment_charge_usd": str(
                ledger.incremental_experiment_charge_usd
            ),
            "informational_list_price_equivalent_usd": str(
                ledger.incremental_informational_list_price_equivalent_usd
            ),
            "unresolved_invocations": len(ledger.unresolved),
            "journal_integrity": journal.integrity_report(),
            "invocation_integrity": invocations.integrity_report(),
            "subprocesses_closed": transport.subprocesses_closed,
            "human_gate": "D5.10_not_evaluated_human_owned",
        }

    try:
        for job in jobs:
            if time.monotonic() - started >= maximum_elapsed_seconds:
                stop_reason = "runtime_window_exhausted"
                break
            mode = str(job["mode"])
            policy = CliMemoryPolicy(
                claude.ClaudeCodePolicy(),
                retain_screenshots=mode == "history",
            )
            runner = CliMemoryRunner(
                journal=journal,
                manifest=manifests[mode],
                policy=policy,
                transport=transport,
                approved_caps=APPROVED_CAPS,
                time_exhausted=lambda mode=mode: (
                    time.monotonic() - started
                    >= maximum_elapsed_seconds
                    - manifests[mode].request_deadline_seconds
                ),
            )
            journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_started",
                kind="d59_assignment_started",
                trial_id=str(job["trial_id"]),
                step_index=0,
                payload={
                    "job": job,
                    "execution_plan_digest": EXECUTION_PLAN_DIGEST,
                },
            )
            result = runner.run(
                trial_id=str(job["trial_id"]),
                task=generate_memory_task(int(job["seed"])),
                backend=FocusMemoryBackend(),
            ).to_dict()
            row = {
                **job,
                **result,
                **episode_measurements(
                    journal, str(job["trial_id"]), int(job["seed"])
                ),
            }
            journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_completed",
                kind="d59_assignment_completed",
                trial_id=str(job["trial_id"]),
                step_index=int(result["environment_actions"]),
                payload=row,
            )
            rows.append(row)
            write_object(output / "summary.json", summary())
            print(
                json.dumps(
                    {
                        "completed": len(rows),
                        "assigned": len(jobs),
                        "phase": job["phase"],
                        "seed": job["seed"],
                        "mode": mode,
                        "repeat": job["repeat"],
                        "classification": row["classification"],
                        "environment_actions": result["environment_actions"],
                        "model_attempts": result["model_attempts"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if row["classification"] not in TERMINAL_BENCHMARK_OUTCOMES or ledger.blocked:
                stop_reason = (
                    "invocation_ledger_blocked"
                    if ledger.blocked
                    else str(row["classification"])
                )
                break
        else:
            stop_reason = "completed_all_assignments"
    except BaseException as exc:
        stop_reason = "execution_error"
        error = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        try:
            transport.close()
            write_object(output / "summary.json", summary())
        finally:
            try:
                invocations.close()
            finally:
                journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output)
    else:
        execute(output)


if __name__ == "__main__":
    main()
