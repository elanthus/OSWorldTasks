"""Prepare or execute one approved phase of the D5.9 replication under the kernel sandbox.

``prepare`` validates the committed plan, the phase approval, the live route (``claude auth
status`` under the launch environment), and the sandbox probe, then writes the bound
inputs to a fresh output directory. It makes no provider call. ``execute`` re-validates,
runs the phase's assignments under the pre-registered stop rule, and captures the run
directory into the retention archive and backup before exiting. Nothing is ever replayed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy, CliMemoryRunner
from pixelgym.grounding.v5.cli_sandbox_probe import run_cli_sandbox_probe
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from pixelgym.grounding.v5.d59_replication import (
    OUTPUT_DIRECTORY,
    SOURCE_FILES,
    STOP_RULE_VERSION,
    campaign_stop_reason,
    execution_plan,
    live_manifests,
    phase_jobs,
    validate_approval,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.retention import backup_archive, create_archive
from pixelgym.grounding.v5.screenshot_memory import require_clean_tracked_worktree
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = OUTPUT_DIRECTORY + "/execution-plan.json"


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    temporary.replace(path)


def placeholder_token() -> str:
    """Synthetic, credential-shaped value for the no-cost sandbox probe only."""

    return "".join(("s", "k", "-ant-oat01-placeholder-", "0" * 24))


def cli_executable() -> Path:
    located = shutil.which("claude")
    if located is None:
        raise RuntimeError("claude is not installed")
    return Path(located).resolve()


def route_probe_environment(temporary: Path) -> dict[str, str]:
    """An isolated home and config so `auth status` reports the token route, not the Keychain."""

    token = os.environ.get(claude.OAUTH_TOKEN_ENVIRONMENT_VARIABLE, "")
    if not token:
        raise RuntimeError(
            f"{claude.OAUTH_TOKEN_ENVIRONMENT_VARIABLE} is not set in the runner environment"
        )
    home = temporary / "home"
    config = temporary / "config"
    home.mkdir()
    config.mkdir()
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "CLAUDE_CONFIG_DIR": str(config),
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        claude.OAUTH_TOKEN_ENVIRONMENT_VARIABLE: token,
    }


def confirmed_live_identity(plan: dict[str, Any]) -> claude.ClaudeRuntimeIdentity:
    """The route the plan declares must be the route the installed CLI reports now."""

    with tempfile.TemporaryDirectory(prefix="pixelgym-d59-replication-route-") as temporary:
        identity = claude.probe_claude_runtime(environment=route_probe_environment(Path(temporary)))
    if identity.to_dict() != plan["runtime_identity"]:
        raise ValueError("live Claude Code identity differs from the frozen replication route")
    return identity


def sandbox_launch(output: Path) -> claude.ClaudeSandboxLaunch:
    return claude.ClaudeSandboxLaunch.from_environment(
        cli_executable=cli_executable(), denied_paths=(output,)
    )


def prepared_inputs(
    phase: str, approval_path: Path, output: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], Any, dict[str, Any]]:
    require_clean_tracked_worktree(ROOT)
    plan = read_object(ROOT / PLAN_PATH)
    frozen_identity = claude.ClaudeRuntimeIdentity(**plan["runtime_identity"])
    expected = execution_plan(
        ROOT,
        source_revision=plan["source_binding"]["source_revision"],
        runtime_identity=frozen_identity,
    )
    if plan != expected:
        raise ValueError("replication plan differs from the committed candidate")
    approval = read_object(approval_path)
    validate_approval(plan, approval, phase=phase)
    identity = confirmed_live_identity(plan)
    manifests = live_manifests(
        ROOT, source_revision=plan["source_binding"]["source_revision"], identity=identity
    )
    if {k: v.to_dict() for k, v in manifests.items()} != plan["policy_manifests"]:
        raise ValueError("live policies differ from the replication plan")
    executable = cli_executable()
    probe = run_cli_sandbox_probe(cli_executable=executable, placeholder_token=placeholder_token())
    if not probe.passed:
        raise ValueError("the CLI sandbox probe did not pass on this host")
    jobs = phase_jobs(plan, phase)
    caps = CallCaps.calculate(
        max_episode_steps=tuple(int(job["action_limit"]) for job in jobs),
        max_model_attempts_per_action=2,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
    )
    if caps.to_dict() != approval["approved_caps"]:
        raise ValueError("phase assignments do not reproduce the approved caps")
    binding = {
        "schema_version": "pixelgym-agent-v5-d59-replication-binding-v1",
        "phase": phase,
        "execution_plan_digest": content_digest(plan),
        "owner_approval_digest": content_digest(approval),
        "runtime_identity": identity.to_dict(),
        "cli_executable_sha256": sha256_file(executable),
        "sandbox_probe": probe.to_dict(),
        "source_files": {path: sha256_file(ROOT / path) for path in SOURCE_FILES},
        "policy_manifest_digests": {
            mode: content_digest(manifest.to_dict()) for mode, manifest in manifests.items()
        },
        "approved_caps": caps.to_dict(),
        "stop_rule_version": STOP_RULE_VERSION,
        "provider_calls_made_during_binding": 0,
    }
    del output
    return plan, approval, jobs, identity, binding


def prepare(phase: str, approval_path: Path, output: Path) -> None:
    plan, approval, jobs, _identity, binding = prepared_inputs(phase, approval_path, output)
    output.mkdir(parents=True, exist_ok=False)
    write_object(output / "execution-plan.json", plan)
    write_object(output / "owner-approval.json", approval)
    write_object(output / "execution-binding.json", binding)
    print(
        json.dumps(
            {
                "phase": phase,
                "execution_plan_digest": plan["execution_plan_digest"],
                "assignments": len(jobs),
                "conditions": dict(Counter(str(job["mode"]) for job in jobs)),
                "approved_caps": binding["approved_caps"],
                "approved_runtime_window_hours": approval["approved_runtime_window_hours"],
                "sandbox_probe_passed": binding["sandbox_probe"]["passed"],
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


def retain(output: Path, archive: Path, backup: Path) -> dict[str, Any]:
    """Capture the run directory and verify a second copy; never deletes the source."""

    created = create_archive(output, archive)
    digest = str(created["manifest_digest"])
    backed = backup_archive(archive, backup, expected_digest=digest)
    return {"archive": created, "backup": backed, "manifest_digest": digest}


def execute(
    phase: str,
    approval_path: Path,
    output: Path,
    *,
    retention_archive: Path,
    retention_backup: Path,
) -> None:
    plan, approval, jobs, identity, binding = prepared_inputs(phase, approval_path, output)
    if read_object(output / "execution-plan.json") != plan:
        raise ValueError("prepared execution plan differs from the approved plan")
    if read_object(output / "owner-approval.json") != approval:
        raise ValueError("prepared owner approval differs from the approved record")
    if read_object(output / "execution-binding.json") != binding:
        raise ValueError("prepared runtime binding differs from the live runtime")
    if (output / "attempts.sqlite").exists() or (output / "invocations.sqlite").exists():
        raise FileExistsError("never restart or overwrite an existing replication campaign")
    if retention_archive.exists() or retention_backup.exists():
        raise FileExistsError("retention destinations must not exist before the run")

    manifests = live_manifests(
        ROOT, source_revision=plan["source_binding"]["source_revision"], identity=identity
    )
    approved_caps = CallCaps(**approval["approved_caps"])
    ledger = SubscriptionExemptLedger(Decimal("1.00"), Decimal("0.00"))
    invocations = claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    transport = claude.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocations,
        runtime_identity=identity,
        expected_resolved_model=claude.MODEL,
        allow_timeout_retry=True,
        api_retry_limit=0,
        allow_connection_retry=True,
        sandbox_launch=sandbox_launch(output),
    )
    journal = V5AttemptJournal(output / "attempts.sqlite")
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    maximum_elapsed_seconds = int(approval["approved_runtime_window_hours"]) * 3600
    stop_reason = "running"
    error: dict[str, str] | None = None
    consecutive_infrastructure_failures = 0
    retention: dict[str, Any] | None = None

    def attempted_assignments() -> int:
        return sum(event.kind == "d59_replication_assignment_started" for event in journal.events())

    def phase_usage(selected_phase: str) -> dict[str, int]:
        selected = [row for row in rows if row["phase"] == selected_phase]
        return {
            "environment_actions": sum(int(row["environment_actions"]) for row in selected),
            "model_attempts": sum(int(row["model_attempts"]) for row in selected),
            "provider_control_requests": sum(
                int(row["provider_control_requests"]) for row in selected
            ),
            "provider_wire_requests": sum(int(row["provider_wire_requests"]) for row in selected),
        }

    def egress_totals() -> dict[str, int]:
        allowed = refused = 0
        for record in transport.records:
            attempts = record.get("egress_attempts") or {}
            allowed += int(attempts.get("allowed_count", 0))
            refused += int(attempts.get("refused_count", 0))
        return {"allowed_connects": allowed, "refused_connects": refused}

    def summary() -> dict[str, Any]:
        return {
            "schema_version": "pixelgym-agent-v5-d59-replication-summary-v1",
            "phase": phase,
            "execution_plan_digest": plan["execution_plan_digest"],
            "execution_binding_digest": content_digest(binding),
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "model": claude.MODEL,
            "provider": claude.PROVIDER_IDENTITY,
            "route": identity.to_dict(),
            "stop_rule_version": STOP_RULE_VERSION,
            "assigned": len(jobs),
            "attempted": attempted_assignments(),
            "completed": len(rows),
            "unrun": len(jobs) - attempted_assignments(),
            "stop_reason": stop_reason,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "approved_runtime_seconds": maximum_elapsed_seconds,
            "results": rows,
            "counts": {
                selected_phase: {
                    mode: dict(
                        Counter(
                            row["classification"]
                            for row in rows
                            if row["phase"] == selected_phase and row["mode"] == mode
                        )
                    )
                    for mode in ("history", "stateless")
                }
                for selected_phase in ("primary", "reliability")
            },
            "phase_usage": {p: phase_usage(p) for p in ("primary", "reliability")},
            "egress": egress_totals(),
            "provider_processes_started": ledger.processes_started,
            "incremental_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
            "unresolved_invocations": len(ledger.unresolved),
            "journal_integrity": journal.integrity_report(),
            "invocation_integrity": invocations.integrity_report(),
            "subprocesses_closed": transport.subprocesses_closed,
            "retention": retention,
            "human_gate": "D5.10_not_evaluated_human_owned",
        }

    try:
        for job in jobs:
            if time.monotonic() - started >= maximum_elapsed_seconds:
                stop_reason = "runtime_window_exhausted"
                break
            mode = str(job["mode"])
            runner = CliMemoryRunner(
                journal=journal,
                manifest=manifests[mode],
                policy=CliMemoryPolicy(
                    claude.ClaudeCodePolicy(), retain_screenshots=mode == "history"
                ),
                transport=transport,
                approved_caps=approved_caps,
                time_exhausted=lambda mode=mode: (
                    time.monotonic() - started
                    >= maximum_elapsed_seconds - manifests[mode].request_deadline_seconds
                ),
            )
            journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_started",
                kind="d59_replication_assignment_started",
                trial_id=str(job["trial_id"]),
                step_index=0,
                payload={"job": job, "execution_plan_digest": plan["execution_plan_digest"]},
            )
            result = runner.run(
                trial_id=str(job["trial_id"]),
                task=generate_memory_task(int(job["seed"])),
                backend=FocusMemoryBackend(),
            ).to_dict()
            row = {
                **job,
                **result,
                **episode_measurements(journal, str(job["trial_id"]), int(job["seed"])),
            }
            journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_completed",
                kind="d59_replication_assignment_completed",
                trial_id=str(job["trial_id"]),
                step_index=int(result["environment_actions"]),
                payload=row,
            )
            rows.append(row)
            if row["classification"] == "infrastructure_failure":
                consecutive_infrastructure_failures += 1
            else:
                consecutive_infrastructure_failures = 0
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
            reason = campaign_stop_reason(
                str(row["classification"]),
                ledger_blocked=ledger.blocked,
                consecutive_infrastructure_failures=consecutive_infrastructure_failures,
            )
            if reason is not None:
                stop_reason = reason
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
        retention = retain(output, retention_archive, retention_backup)
        write_object(output / "summary.json", summary())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--phase", choices=("pilot", "full"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--retention-archive", type=Path)
    parser.add_argument("--retention-backup", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(args.phase, args.approval.resolve(), output)
        return
    if args.retention_archive is None or args.retention_backup is None:
        parser.error("execute requires --retention-archive and --retention-backup")
    execute(
        args.phase,
        args.approval.resolve(),
        output,
        retention_archive=args.retention_archive.resolve(),
        retention_backup=args.retention_backup.resolve(),
    )


if __name__ == "__main__":
    main()
