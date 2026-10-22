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
import queue
import shutil
import sys
import tempfile
import threading
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
    MAX_WORKERS,
    OUTPUT_DIRECTORY,
    SOURCE_FILES,
    execution_plan,
    live_manifests,
    phase_jobs,
    run_campaign,
    stop_rule_options_for_plan,
    validate_approval,
)
from pixelgym.grounding.v5.journal import CallCapExceededError, V5AttemptJournal
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


def confirmed_live_identity(
    plan: dict[str, Any],
) -> tuple[claude.ClaudeRuntimeIdentity, dict[str, Any]]:
    """The route the plan declares must be the route the installed CLI reports now."""

    proofs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pixelgym-d59-replication-route-") as temporary:
        identity = claude.probe_claude_runtime(
            environment=route_probe_environment(Path(temporary)),
            on_subscription_verified=proofs.append,
        )
    if identity.to_dict() != plan["runtime_identity"]:
        raise ValueError("live Claude Code identity differs from the frozen replication route")
    if len(proofs) != 1:
        raise ValueError("the token route requires fresh same-credential Max verification")
    return identity, proofs[0]


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
        stop_rule_options=stop_rule_options_for_plan(plan),
    )
    if plan != expected:
        raise ValueError("replication plan differs from the committed candidate")
    approval = read_object(approval_path)
    validate_approval(plan, approval, phase=phase)
    identity, subscription_verification = confirmed_live_identity(plan)
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
        "subscription_verification": subscription_verification,
        "cli_executable_sha256": sha256_file(executable),
        "sandbox_probe": probe.to_dict(),
        "source_files": {path: sha256_file(ROOT / path) for path in SOURCE_FILES},
        "policy_manifest_digests": {
            mode: content_digest(manifest.to_dict()) for mode, manifest in manifests.items()
        },
        "approved_caps": caps.to_dict(),
        "stop_rule_version": plan["stop_rule"]["version"],
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


def assignment_call_usage(journal: V5AttemptJournal, trial_id: str) -> dict[str, int]:
    """Count this assignment's durable reservations, including retries and unknowns.

    Runner result counters subtract run-wide snapshots, which overlap when workers
    share a journal. Trial-scoped events retain the actual attribution.
    """

    events = journal.events(trial_id)
    model = sum(
        int(event.payload["model_attempt_reservation"])
        for event in events
        if event.kind == "attempt_started"
    )
    control = sum(
        int(event.payload["control_request_reservation"])
        for event in events
        if event.kind == "provider_control_request_reserved"
    )
    return {
        "model_attempts": model,
        "provider_control_requests": control,
        "provider_wire_requests": model + control,
    }


def assignment_rate_limit_metadata(journal: V5AttemptJournal, trial_id: str) -> dict[str, Any]:
    """Use recorded fault facts rather than inferring a rate limit from a score label."""

    events = journal.events(trial_id)
    faults = [event.payload["cli_fault"] for event in events if event.payload.get("cli_fault")]
    rate_limits = [
        event
        for event in events
        if event.payload.get("cli_fault", {}).get("kind") == "subscription_rate_limit"
    ]
    hints = [
        event.payload["retry_after_seconds"]
        for event in rate_limits
        if isinstance(event.payload.get("retry_after_seconds"), (int, float))
    ]
    terminal_fault = faults[-1] if faults else {}
    return {
        "rate_limited": bool(rate_limits),
        "retry_after_seconds": max(hints, default=None),
        "request_failure_phase": terminal_fault.get("phase"),
        "request_failure_kind": terminal_fault.get("kind"),
    }


def execute(
    phase: str,
    approval_path: Path,
    output: Path,
    *,
    retention_archive: Path,
    retention_backup: Path,
    workers: int,
) -> None:
    plan, approval, jobs, identity, binding = prepared_inputs(phase, approval_path, output)
    stop_options = stop_rule_options_for_plan(plan)
    if read_object(output / "execution-plan.json") != plan:
        raise ValueError("prepared execution plan differs from the approved plan")
    if read_object(output / "owner-approval.json") != approval:
        raise ValueError("prepared owner approval differs from the approved record")
    if read_object(output / "execution-binding.json") != binding:
        raise ValueError("prepared runtime binding differs from the live runtime")
    if (output / "attempts.sqlite").exists() or list(output.glob("invocations-*.sqlite")):
        raise FileExistsError("never restart or overwrite an existing replication campaign")
    if retention_archive.exists() or retention_backup.exists():
        raise FileExistsError("retention destinations must not exist before the run")
    if not 1 <= workers <= int(plan["concurrency"]["max_workers"]):
        raise ValueError("workers exceeds the pre-registered maximum")

    manifests = live_manifests(
        ROOT, source_revision=plan["source_binding"]["source_revision"], identity=identity
    )
    approved_caps = CallCaps(**approval["approved_caps"])
    ledger = SubscriptionExemptLedger(Decimal("1.00"), Decimal("0.00"))
    launch = sandbox_launch(output)
    # One transport per worker: its own egress proxy and invocation journal. The attempt
    # journal file is shared through one connection per worker.
    journal_path = output / "attempts.sqlite"
    journal = V5AttemptJournal(journal_path)
    journals = [journal] + [V5AttemptJournal(journal_path) for _ in range(workers - 1)]
    summary_journal = V5AttemptJournal(journal_path, read_only=True)
    invocations = [
        claude.ClaudeInvocationJournal(output / f"invocations-{index}.sqlite")
        for index in range(workers)
    ]
    transports = [
        claude.ClaudeCodeTransport(
            ledger=ledger,
            invocation_journal=invocations[index],
            runtime_identity=identity,
            expected_resolved_model=claude.MODEL,
            allow_timeout_retry=True,
            api_retry_limit=0,
            allow_connection_retry=True,
            sandbox_launch=launch,
        )
        for index in range(workers)
    ]
    free: queue.Queue[int] = queue.Queue()
    for index in range(workers):
        free.put(index)
    rows: list[dict[str, Any]] = []
    rows_lock = threading.Lock()
    assignments_started = 0
    assignments_lock = threading.Lock()
    source_revision = str(plan["source_binding"]["source_revision"])
    started = time.monotonic()
    maximum_elapsed_seconds = int(approval["approved_runtime_window_hours"]) * 3600
    stop_reason = "running"
    error: dict[str, str] | None = None
    retention: dict[str, Any] | None = None
    execution_failed = False

    def attempted_assignments() -> int:
        with assignments_lock:
            return assignments_started

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
        for transport in transports:
            for record in list(transport.records):
                attempts = record.get("egress_attempts") or {}
                allowed += int(attempts.get("allowed_count", 0))
                refused += int(attempts.get("refused_count", 0))
        return {"allowed_connects": allowed, "refused_connects": refused}

    def window_exhausted() -> bool:
        return time.monotonic() - started >= maximum_elapsed_seconds

    def summary() -> dict[str, Any]:
        with rows_lock:
            snapshot = list(rows)
        attempted = attempted_assignments()
        return {
            "schema_version": "pixelgym-agent-v5-d59-replication-summary-v1",
            "phase": phase,
            "execution_plan_digest": plan["execution_plan_digest"],
            "execution_binding_digest": content_digest(binding),
            "source_revision": source_revision,
            "model": claude.MODEL,
            "provider": claude.PROVIDER_IDENTITY,
            "route": identity.to_dict(),
            "stop_rule_version": plan["stop_rule"]["version"],
            "workers": workers,
            "assigned": len(jobs),
            "attempted": attempted,
            "completed": len(snapshot),
            "unrun": len(jobs) - attempted,
            "stop_reason": stop_reason,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "approved_runtime_seconds": maximum_elapsed_seconds,
            "results": sorted(snapshot, key=lambda row: int(row["plan_index"])),
            "counts": {
                selected_phase: {
                    mode: dict(
                        Counter(
                            row["classification"]
                            for row in snapshot
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
            "journal_integrity": summary_journal.integrity_report(),
            "invocation_integrity": [invocation.integrity_report() for invocation in invocations],
            "subprocesses_closed": all(t.subprocesses_closed for t in transports),
            "retention": retention,
            "human_gate": "D5.10_not_evaluated_human_owned",
        }

    def run_assignment(job: dict[str, Any]) -> dict[str, Any]:
        nonlocal assignments_started
        worker = free.get()
        try:
            mode = str(job["mode"])
            worker_journal = journals[worker]
            runner = CliMemoryRunner(
                journal=worker_journal,
                manifest=manifests[mode],
                policy=CliMemoryPolicy(
                    claude.ClaudeCodePolicy(runtime_identity=identity),
                    retain_screenshots=mode == "history",
                ),
                transport=transports[worker],
                approved_caps=approved_caps,
                time_exhausted=lambda mode=mode: (
                    time.monotonic() - started
                    >= maximum_elapsed_seconds - manifests[mode].request_deadline_seconds
                ),
            )
            worker_journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_started",
                kind="d59_replication_assignment_started",
                trial_id=str(job["trial_id"]),
                step_index=0,
                payload={
                    "job": job,
                    "worker": worker,
                    "execution_plan_digest": plan["execution_plan_digest"],
                },
            )
            with assignments_lock:
                assignments_started += 1
            task = generate_memory_task(int(job["seed"]))
            try:
                result = runner.run(
                    trial_id=str(job["trial_id"]),
                    task=task,
                    backend=FocusMemoryBackend(),
                ).to_dict()
            except CallCapExceededError:
                # A partial assignment remains in the denominator and retains its
                # actual dispatch/usage history. No checkpoint is fabricated.
                result = {
                    "trial_id": str(job["trial_id"]),
                    "task_id": task.task_id,
                    "success": False,
                    "classification": "approved_cap_reached",
                    "incomplete_assignment": True,
                    "environment_actions": sum(
                        event.kind == "dispatch_committed"
                        for event in worker_journal.events(str(job["trial_id"]))
                    ),
                    "final_policy_checkpoint_digest": None,
                }
            row = {
                **job,
                **result,
                **episode_measurements(worker_journal, str(job["trial_id"]), int(job["seed"])),
                **assignment_call_usage(worker_journal, str(job["trial_id"])),
                **(
                    assignment_rate_limit_metadata(worker_journal, str(job["trial_id"]))
                    if stop_options is not None
                    else {}
                ),
                "worker": worker,
            }
            worker_journal.append_event(
                event_key=str(job["trial_id"]) + "/assignment_completed",
                kind="d59_replication_assignment_completed",
                trial_id=str(job["trial_id"]),
                step_index=int(result["environment_actions"]),
                payload=row,
            )
            return row
        finally:
            free.put(worker)

    def on_result(row: dict[str, Any]) -> None:
        with rows_lock:
            rows.append(row)
        write_object(output / "summary.json", summary())
        print(
            json.dumps(
                {
                    "completed": int(row["completion_index"]) + 1,
                    "assigned": len(jobs),
                    "phase": row["phase"],
                    "seed": row["seed"],
                    "mode": row["mode"],
                    "repeat": row["repeat"],
                    "worker": row["worker"],
                    "classification": row["classification"],
                    "environment_actions": row["environment_actions"],
                    "model_attempts": row["model_attempts"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    def on_backoff(receipt: dict[str, Any]) -> None:
        journal.append_event(
            event_key=(
                f"{receipt['trial_id']}/rate_limit_backoff/{receipt['stage']}"
                + (f"/{receipt['wait_index']}" if "wait_index" in receipt else "")
            ),
            kind="d59_replication_rate_limit_backoff",
            trial_id=str(receipt["trial_id"]),
            step_index=0,
            payload=receipt,
        )

    def wait_for_backoff(seconds: float) -> None:
        remaining = max(0.0, maximum_elapsed_seconds - (time.monotonic() - started))
        threading.Event().wait(timeout=min(seconds, remaining))

    try:
        campaign = run_campaign(
            jobs,
            workers=workers,
            run_assignment=run_assignment,
            window_exhausted=window_exhausted,
            ledger_blocked=lambda: ledger.blocked,
            on_result=on_result,
            stop_rule_version=plan["stop_rule"]["version"],
            stop_rule_options=stop_options,
            on_backoff=on_backoff,
            wait_for_backoff=wait_for_backoff,
        )
        stop_reason = campaign.stop_reason
    except BaseException as exc:
        stop_reason = "execution_error"
        error = {"type": type(exc).__name__, "message": str(exc)}
        execution_failed = True
        raise
    finally:
        # Snapshot the summary while the journals are open, close everything, then
        # archive. The archive holds the snapshot with retention null; the source
        # summary.json gets the receipt merged in. A finalization error never masks an
        # execution error and never hides a completed campaign's results.
        final_summary: dict[str, Any] | None = None
        finalization_error: BaseException | None = None
        try:
            for transport in transports:
                transport.close()
            final_summary = summary()
            write_object(output / "summary.json", final_summary)
        except BaseException as exc:  # noqa: BLE001 - retained, re-raised below
            finalization_error = exc
        for closer in (
            summary_journal.close,
            *(invocation.close for invocation in invocations),
            *(extra.close for extra in journals[1:]),
            journal.close,
        ):
            try:
                closer()
            except BaseException as exc:  # noqa: BLE001 - retained, re-raised below
                finalization_error = finalization_error or exc
        try:
            retention = retain(output, retention_archive, retention_backup)
            if final_summary is not None:
                write_object(output / "summary.json", {**final_summary, "retention": retention})
        except BaseException as exc:  # noqa: BLE001 - retained, re-raised below
            finalization_error = finalization_error or exc
        if finalization_error is not None:
            if not execution_failed:
                raise finalization_error
            # The execution exception is already propagating; report the retained
            # finalization error so a failed close, write, archive, or backup is visible.
            print(
                "finalization error during failed execution: "
                f"{type(finalization_error).__name__}: {finalization_error}",
                file=sys.stderr,
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--phase", choices=("pilot", "full"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--retention-archive", type=Path)
    parser.add_argument("--retention-backup", type=Path)
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"concurrent assignments, 1 to the pre-registered maximum ({MAX_WORKERS})",
    )
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
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
