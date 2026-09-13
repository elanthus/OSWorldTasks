"""Prepare and run the explicitly requested PR196 CLI calibration, one model per process."""

from __future__ import annotations

import argparse
import json
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


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "execute"])
    parser.add_argument("--model", choices=["luna-medium", "haiku-default"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_clean_tracked_worktree(ROOT)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # Freeze all benchmark and evaluator files; only CLI adapter/harness paths may differ.
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", FROZEN, "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    allowed = {
        "pixelgym/grounding/v5/codex_cli_policy.py",
        "pixelgym/grounding/v5/claude_code_policy.py",
        "pixelgym/grounding/v5/cli_memory_calibration.py",
        "scripts/run_grounding_v5_cli_memory.py",
    }
    if any(
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
    jobs = [
        {**j, "trial_id": f"pr196-{args.model}-{j['seed']}-{j['mode']}"} for j in original["jobs"]
    ]
    for job in jobs:
        task = generate_memory_task(job["seed"])
        if (
            task.task_id != job["task_id"]
            or content_digest(task.canonical_dict()) != job["task_digest"]
            or task.max_episode_steps != job["action_limit"]
        ):
            raise ValueError("frozen task mismatch")
    actions = sum(j["action_limit"] for j in jobs)
    caps = CallCaps(actions, actions, 0, actions)
    plan = {
        "schema_version": "pixelgym-pr196-cli-memory-plan-v1",
        "frozen_benchmark_revision": FROZEN,
        "adapter_revision": revision,
        "model": args.model,
        "runtime_identity": identity.to_dict(),
        "source_plan": SOURCE_PLAN,
        "source_plan_digest": content_digest(original),
        "jobs": jobs,
        "policy_manifests": {m: p.to_dict() for m, p in manifests.items()},
        "caps": caps.to_dict(),
        "owner_authorization": "Run both CLI models in parallel; both conditions, 100 each; Haiku default effort; adapt separately from PR196.",
        "transport_retries": 0,
        "maximum_elapsed_seconds": 21600,
        "incremental_experiment_charge_usd": "0.00",
        "billing": "authenticated subscriptions only",
        "stop_rule": "stop on infrastructure/request/policy failures or unresolved invocations; retain invalid outputs; no silent retries",
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
        )
        if is_codex
        else claude.ClaudeCodeTransport(
            ledger=ledger,
            invocation_journal=invocations,
            runtime_identity=identity,
            expected_resolved_model=claude.MODEL,
        )
    )
    journal = V5AttemptJournal(output / "attempts.sqlite")
    rows = []
    started = time.monotonic()
    stop = "completed_all_assignments"
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
                time_exhausted=lambda: (
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
