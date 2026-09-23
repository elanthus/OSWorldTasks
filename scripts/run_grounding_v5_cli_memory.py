"""Prepare and run the explicitly requested PR196 CLI calibration, one model per process."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import codex_cli_policy as codex
from pixelgym.grounding.v5.cli_memory_calibration import (
    CliMemoryPolicy,
    CliMemoryRunner,
    build_memory_manifest,
)
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.screenshot_memory import require_clean_tracked_worktree

ROOT = Path(__file__).resolve().parents[1]
FROZEN = "1e5d9c0d19acf51505919deefe0d155c2ab22b26"
SOURCE_PLAN = "artifacts/grounding-v5-d58-gemini38-calibration/execution-plan.json"
FROZEN_RUNTIME_FILES = (
    "pixelgym/grounding/v5/memory_backend.py",
    "pixelgym/grounding/v5/memory_calibration.py",
    "pixelgym/grounding/v5/memory_focus_backend.py",
    "pixelgym/grounding/v5/screenshot_memory.py",
)


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def unfinished_jobs(jobs, predecessor):
    """Keep terminal outcomes; reset only missing or infrastructure-interrupted jobs."""
    expected = {(j["seed"], j["mode"]): j for j in jobs}
    if len(expected) != len(jobs):
        raise ValueError("duplicate source assignment")
    seen = {}
    terminal = {"success_termination", "step_limit_truncation", "invalid_output"}
    interrupted = {"phase_time_stop", "infrastructure_failure", "request_failure"}
    for row in predecessor["results"]:
        key = (row["seed"], row["mode"])
        if key not in expected or key in seen:
            raise ValueError("unknown or duplicate predecessor result")
        if any(row[k] != expected[key][k] for k in ("task_id", "task_digest", "action_limit")):
            raise ValueError("predecessor assignment differs from frozen task")
        if row["classification"] not in terminal | interrupted:
            raise ValueError("predecessor classification needs review")
        seen[key] = row["classification"]
    return [j for j in jobs if seen.get((j["seed"], j["mode"])) not in terminal]


def fresh_jobs(jobs):
    """Return the complete frozen assignment set after rejecting duplicate identities."""
    identities = [(job["seed"], job["mode"]) for job in jobs]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate source assignment")
    return list(jobs)


def validate_fresh_runtime_surface(root, revision):
    """Keep runtime behavior frozen while task identities bind the evolved generator."""
    for path in FROZEN_RUNTIME_FILES:
        current = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=root)
        frozen = subprocess.check_output(["git", "show", f"{FROZEN}:{path}"], cwd=root)
        if current != frozen:
            raise ValueError(f"fresh runtime source differs from frozen benchmark: {path}")


def validate_fresh_approval(path, plan, caps):
    """Require an exact, separate owner approval before a fresh model call."""
    approval = json.loads(path.read_text())
    expected = {
        "schema_version": "pixelgym-pr196-haiku-cli-replication-approval-v1",
        "execution_plan_digest": content_digest(plan),
        "approved_environment_action_cap": caps.environment_action_cap,
        "approved_model_attempt_cap": caps.model_attempt_cap,
        "approved_provider_control_request_cap": caps.provider_control_request_cap,
        "approved_provider_wire_request_cap": caps.provider_wire_request_cap,
        "approved_runtime_seconds": plan["maximum_elapsed_seconds"],
        "incremental_experiment_charge_cap_usd": "0.00",
        "owner_approved": True,
    }
    if approval != expected:
        raise ValueError("fresh execution approval differs from the exact prepared plan")
    return approval


def validate_predecessor(path, model):
    predecessor = json.loads(path.read_text())
    prior_plan = json.loads(path.with_name("execution-plan.json").read_text())
    if (
        predecessor["model"] != model
        or predecessor["plan_digest"] != content_digest(prior_plan)
        or predecessor["frozen_benchmark_revision"] != FROZEN
        or predecessor["subprocesses_closed"] is not True
    ):
        raise ValueError("predecessor identity or process cleanup mismatch")
    if predecessor["completed"] != len(predecessor["results"]):
        raise ValueError("predecessor result count mismatch")
    invocations = path.with_name("invocations.sqlite")
    unknown = 0
    with sqlite3.connect("file:" + str(invocations.resolve()) + "?mode=ro", uri=True) as conn:
        for status, exit_code, raw in conn.execute(
            "select status,exit_code,outcome from invocations"
        ):
            if status == "response":
                continue
            outcome = json.loads(raw)
            if (
                status != "timeout"
                or exit_code is None
                or outcome.get("cli_fault", {}).get("kind") != "process_timeout"
                or outcome.get("process_confirmed_stopped") is False
            ):
                raise ValueError("predecessor has an unsafe or unreviewed invocation")
            unknown += 1
    if unknown != predecessor["unresolved_invocations"]:
        raise ValueError("predecessor unresolved count mismatch")
    # Durable host dispatch/result evidence must still match the closed summary.
    journal = V5AttemptJournal(path.with_name("attempts.sqlite"))
    try:
        if journal.integrity_report() != predecessor["journal_integrity"]:
            raise ValueError("predecessor journal changed")
        rows = [e.payload for e in journal.events() if e.kind == "cli_memory_assignment_completed"]
        if rows != predecessor["results"]:
            raise ValueError("predecessor results differ from recorded evidence")
    finally:
        journal.close()
    return predecessor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "execute"])
    parser.add_argument("--model", choices=["luna-medium", "haiku-default"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--fresh",
        action="store_true",
        help="run a new cohort containing every frozen assignment",
    )
    source.add_argument("--predecessor-summary", type=Path)
    parser.add_argument("--approval-file", type=Path)
    args = parser.parse_args()
    require_clean_tracked_worktree(ROOT)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # Freeze all benchmark and evaluator files; only CLI adapter/harness paths may differ.
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", FROZEN, "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    allowed = {
        "pixelgym/grounding/v5/codex_cli_policy.py",
        "pixelgym/grounding/v5/runner.py",
        "pixelgym/grounding/v5/claude_code_policy.py",
        "pixelgym/grounding/v5/cli_memory_calibration.py",
        "scripts/run_grounding_v5_cli_memory.py",
    }
    if not args.fresh and any(
        p not in allowed
        and not p.startswith("tests/unit/test_grounding_v5_cli_memory")
        and p
        not in {
            "tests/unit/test_grounding_v5_claude_code_policy.py",
            "tests/unit/test_grounding_v5_codex_cli_policy.py",
            "tests/unit/test_grounding_v5_claude_code_transport.py",
        }
        for p in changed
    ):
        raise ValueError("unexpected change outside CLI calibration successor")
    is_codex = args.model == "luna-medium"
    identity = (
        codex.probe_codex_runtime(codex.LUNA_MEDIUM) if is_codex else claude.probe_claude_runtime()
    )
    base = (
        codex.build_codex_cli_policy_manifest(
            ROOT, code_revision=revision, runtime_identity=identity, config=codex.LUNA_MEDIUM
        )
        if is_codex
        else claude.build_claude_policy_manifest(
            ROOT, code_revision=revision, runtime_identity=identity, resolved_model=claude.MODEL
        )
    )
    manifests = {
        mode: build_memory_manifest(ROOT, base, retain_screenshots=mode == "history")
        for mode in ("history", "stateless")
    }
    original = json.loads((ROOT / SOURCE_PLAN).read_text())
    campaign = "fresh-replication-v1" if args.fresh else "timeout-continuation-v1"
    jobs = [
        {**j, "trial_id": f"pr196-{args.model}-{j['seed']}-{j['mode']}-{campaign}"}
        for j in original["jobs"]
    ]
    for job in jobs:
        task = generate_memory_task(job["seed"])
        if (
            task.task_id != job["task_id"]
            or content_digest(task.canonical_dict()) != job["task_digest"]
            or task.max_episode_steps != job["action_limit"]
        ):
            raise ValueError("frozen task mismatch")
    predecessor = None
    if args.fresh:
        if args.model != "haiku-default":
            raise ValueError("the approved fresh replication is Haiku-only")
        jobs = fresh_jobs(jobs)
        validate_fresh_runtime_surface(ROOT, revision)
    else:
        predecessor = validate_predecessor(args.predecessor_summary, args.model)
        jobs = unfinished_jobs(jobs, predecessor)
        if not jobs:
            raise ValueError("no unfinished assignments")
    actions = sum(j["action_limit"] for j in jobs)
    caps = CallCaps(actions, actions * 2, 0, actions * 2)
    plan = {
        "schema_version": (
            "pixelgym-pr196-haiku-cli-replication-plan-v1"
            if args.fresh
            else "pixelgym-pr196-cli-memory-plan-v1"
        ),
        "frozen_benchmark_revision": FROZEN,
        "adapter_revision": revision,
        "model": args.model,
        "runtime_identity": identity.to_dict(),
        "source_plan": SOURCE_PLAN,
        "source_plan_digest": content_digest(original),
        "jobs": jobs,
        "policy_manifests": {m: p.to_dict() for m, p in manifests.items()},
        "caps": caps.to_dict(),
        "owner_authorization": (
            "Fresh 100-episode matched Haiku calibration requested and scope confirmed "
            "on 2026-09-20; exact derived caps and runtime still require approval before execute."
            if args.fresh
            else "Please add a retry for CLI timeouts, then retry the ones that did not finish. "
            "One retry after confirmed process stop; unfinished assignments only, both models."
        ),
        "transport_retries": 1,
        "maximum_elapsed_seconds": 43200,
        "incremental_experiment_charge_usd": "0.00",
        "billing": "authenticated subscriptions only",
        "execution_authorized": False,
        "stop_rule": "stop on infrastructure/request/policy failures or unresolved invocations; retain invalid outputs; no silent retries",
    }
    if predecessor is not None:
        # Unknown provider completion stays recorded; exited CLI cannot dispatch later.
        plan["predecessor"] = {
            "summary_digest": content_digest(predecessor),
            "plan_digest": predecessor["plan_digest"],
            "completed": predecessor["completed"],
            "stop_reason": predecessor["stop_reason"],
            "rule": "retain finished outcomes; interrupted episodes reset; preserve prior timeouts and disclose changed retry policy",
            "unresolved_provider_outcomes_retained": predecessor["unresolved_invocations"],
        }
    else:
        plan["fresh_cohort"] = {
            "prior_outcomes_reused": 0,
            "assignments": len(jobs),
            "conditions": dict(sorted(Counter(job["mode"] for job in jobs).items())),
            "confirmatory_tasks_exposed": 0,
        }
    output = args.output.resolve()
    if args.mode == "prepare":
        output.mkdir(parents=True, exist_ok=False)
        write(output / "execution-plan.json", plan)
        print(
            json.dumps(
                {
                    "plan_digest": content_digest(plan),
                    "episodes": len(jobs),
                    "caps": caps.to_dict(),
                    "provider_calls": 0,
                }
            )
        )
        return
    if json.loads((output / "execution-plan.json").read_text()) != plan:
        raise ValueError("runtime differs from prepared plan")
    if args.fresh:
        if args.approval_file is None:
            raise ValueError("fresh execution requires an exact owner approval file")
        validate_fresh_approval(args.approval_file, plan, caps)
    elif args.approval_file is not None:
        raise ValueError("continuation execution does not accept a fresh approval file")
    if (output / "attempts.sqlite").exists():
        raise FileExistsError("never restart or overwrite an existing campaign")
    ledger = codex.SubscriptionExemptLedger(Decimal(1), Decimal(0))
    invocations = (
        codex.CodexCliInvocationJournal(output / "invocations.sqlite", codex.LUNA_MEDIUM)
        if is_codex
        else claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    )
    transport = (
        codex.CodexCliTransport(
            ledger=ledger,
            invocation_journal=invocations,
            runtime_identity=identity,
            config=codex.LUNA_MEDIUM,
            allow_timeout_retry=True,
        )
        if is_codex
        else claude.ClaudeCodeTransport(
            ledger=ledger,
            invocation_journal=invocations,
            runtime_identity=identity,
            expected_resolved_model=claude.MODEL,
            allow_timeout_retry=True,
        )
    )
    journal = V5AttemptJournal(output / "attempts.sqlite")
    rows = []
    started = time.monotonic()
    stop = "running"
    error = None

    def summary():
        return {
            "plan_digest": content_digest(plan),
            "frozen_benchmark_revision": FROZEN,
            "adapter_revision": revision,
            "model": args.model,
            "assigned": len(jobs),
            "completed": len(rows),
            "unrun": len(jobs) - len(rows),
            "stop_reason": stop,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "results": rows,
            "counts": {
                mode: dict(Counter(r["classification"] for r in rows if r["mode"] == mode))
                for mode in manifests
            },
            "provider_processes_started": ledger.processes_started,
            "incremental_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
            "unresolved_invocations": len(ledger.unresolved),
            "journal_integrity": journal.integrity_report(),
            "invocation_integrity": invocations.integrity_report(),
            "subprocesses_closed": transport.subprocesses_closed,
        }

    try:
        for job in jobs:
            if time.monotonic() - started > plan["maximum_elapsed_seconds"]:
                stop = "phase_time_stop"
                break
            policy = CliMemoryPolicy(
                codex.CodexCliPolicy(codex.LUNA_MEDIUM) if is_codex else claude.ClaudeCodePolicy(),
                retain_screenshots=job["mode"] == "history",
            )
            runner = CliMemoryRunner(
                journal=journal,
                manifest=manifests[job["mode"]],
                policy=policy,
                transport=transport,
                approved_caps=caps,
                time_exhausted=lambda job=job: (
                    time.monotonic() - started
                    >= plan["maximum_elapsed_seconds"]
                    - manifests[job["mode"]].request_deadline_seconds
                ),
            )
            journal.append_event(
                event_key=job["trial_id"] + "/assignment_started",
                kind="cli_memory_assignment_started",
                trial_id=job["trial_id"],
                step_index=0,
                payload={"job": job, "plan_digest": content_digest(plan)},
            )
            result = runner.run(
                trial_id=job["trial_id"],
                task=generate_memory_task(job["seed"]),
                backend=FocusMemoryBackend(),
            ).to_dict()
            row = {**job, **result, **episode_measurements(journal, job["trial_id"], job["seed"])}
            journal.append_event(
                event_key=job["trial_id"] + "/assignment_completed",
                kind="cli_memory_assignment_completed",
                trial_id=job["trial_id"],
                step_index=result["environment_actions"],
                payload=row,
            )
            rows.append(row)
            write(output / "summary.json", summary())
            print(
                json.dumps(
                    {
                        "completed": len(rows),
                        "seed": job["seed"],
                        "mode": job["mode"],
                        "classification": row["classification"],
                        "actions": result["environment_actions"],
                    }
                ),
                flush=True,
            )
            if (
                result["classification"]
                not in {"success_termination", "step_limit_truncation", "invalid_output"}
                or ledger.blocked
            ):
                stop = (
                    result["classification"] if not ledger.blocked else "invocation_ledger_blocked"
                )
                break
        else:
            stop = "completed_all_assignments"
    except BaseException as exc:
        stop = "execution_error"
        error = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        try:
            transport.close()
            write(output / "summary.json", summary())
        finally:
            try:
                invocations.close()
            finally:
                journal.close()


if __name__ == "__main__":
    main()
