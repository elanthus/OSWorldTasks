"""Run only unfinished PR196 Haiku assignments under the owner's $20 API cap."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryRunner
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.haiku_openrouter import (
    HaikuOpenRouterPolicy,
    build_manifest,
    config_from_snapshot,
    full_context_bound,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger, episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.reliable_memory import CooldownExecutor
from pixelgym.grounding.v5.reliable_transport import ReliableTransport
from pixelgym.grounding.v5.screenshot_memory import require_clean_tracked_worktree
from scripts.run_grounding_v5_cli_memory import FROZEN, unfinished_jobs, write

ROOT = Path(__file__).resolve().parents[1]
PRIOR = Path(
    "/private/tmp/osworld-pr196-cli-retry/artifacts/pr196-cli-calibration/haiku-default-continuation-v1"
)
OLDER = Path(
    "/private/tmp/osworld-pr196-haiku-json/artifacts/pr196-cli-calibration/haiku-default-v4"
)


def prior_evidence(directory):
    plan = json.loads((directory / "execution-plan.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    if (
        summary["plan_digest"] != content_digest(plan)
        or not summary["subprocesses_closed"]
        or summary["frozen_benchmark_revision"] != FROZEN
    ):
        raise ValueError("predecessor identity/cleanup mismatch")
    journal = V5AttemptJournal(directory / "attempts.sqlite")
    try:
        if journal.integrity_report() != summary["journal_integrity"]:
            raise ValueError("predecessor journal changed")
        rows = [e.payload for e in journal.events() if e.kind == "cli_memory_assignment_completed"]
        if rows != summary["results"]:
            raise ValueError("predecessor results changed")
    finally:
        journal.close()
    return plan, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "execute"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pricing", type=Path, required=True)
    args = parser.parse_args()
    require_clean_tracked_worktree(ROOT)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # These are the only production changes allowed relative to the frozen benchmark.
    allowed = {
        "codex_cli_policy.py",
        "claude_code_policy.py",
        "cli_memory_calibration.py",
        "runner.py",
        "reliable_transport.py",
        "haiku_openrouter.py",
    }
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", FROZEN, "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    if any(
        not (
            p.startswith("tests/unit/test_grounding_v5_")
            or p
            in {
                "scripts/run_grounding_v5_cli_memory.py",
                "scripts/run_grounding_v5_haiku_openrouter.py",
            }
            or (p.startswith("pixelgym/grounding/v5/") and Path(p).name in allowed)
        )
        for p in changed
    ):
        raise ValueError("unexpected frozen benchmark change")
    prior_plan, prior = prior_evidence(PRIOR)
    _, older = prior_evidence(OLDER)
    jobs = unfinished_jobs(prior_plan["jobs"], prior)
    terminal = {"success_termination", "step_limit_truncation", "invalid_output"}
    retained = [r for s in (older, prior) for r in s["results"] if r["classification"] in terminal]
    if (
        len(jobs) != 23
        or len(retained) != 77
        or len({(r["seed"], r["mode"]) for r in retained + jobs}) != 100
    ):
        raise ValueError("continuation partition differs from approved 77 retained / 23 remaining")
    jobs = [{**j, "trial_id": f"pr196-haiku-openrouter-{j['seed']}-{j['mode']}"} for j in jobs]
    for j in jobs:
        task = generate_memory_task(j["seed"])
        if (
            task.task_id != j["task_id"]
            or content_digest(task.canonical_dict()) != j["task_digest"]
            or task.max_episode_steps != j["action_limit"]
        ):
            raise ValueError("frozen task mismatch")
    snapshot = json.loads(args.pricing.read_text())
    config = config_from_snapshot(snapshot)
    manifests = {
        m: build_manifest(ROOT, config, revision, m == "history") for m in ("history", "stateless")
    }
    actions = sum(j["action_limit"] for j in jobs)
    caps = CallCaps(actions, actions * 2, 0, actions * 2)
    plan = {
        "frozen_benchmark_revision": FROZEN,
        "adapter_revision": revision,
        "model": config.model,
        "jobs": jobs,
        "retained_terminal_results": retained,
        "predecessors": [
            {"directory": str(d), "summary_digest": content_digest(s)}
            for d, s in ((OLDER, older), (PRIOR, prior))
        ],
        "pricing_snapshot": snapshot,
        "maximum_spend_usd": "20.00",
        "owner_authorization": "Proceed with a $20 cap; switch Haiku to OpenRouter and finish remaining assignments.",
        "maximum_elapsed_seconds": 43200,
        "caps": caps.to_dict(),
        "policy_manifests": {m: p.to_dict() for m, p in manifests.items()},
        "transport_retries": 1,
        "provider_change": "CLI and OpenRouter outcomes remain separately identified; default API thinking enabled, no native effort parameter.",
        "budget_rule": "Reserve full 200000 input plus 4096 output tokens before each wire call; keep unknown charges reserved; never exceed $20 accounted spend.",
    }
    out = args.output.resolve()
    if args.mode == "prepare":
        out.mkdir(parents=True, exist_ok=False)
        write(out / "execution-plan.json", plan)
        print(
            json.dumps(
                {
                    "plan_digest": content_digest(plan),
                    "episodes": len(jobs),
                    "caps": caps.to_dict(),
                    "maximum_spend_usd": "20.00",
                    "provider_calls": 0,
                }
            )
        )
        return
    if (
        json.loads((out / "execution-plan.json").read_text()) != plan
        or (out / "attempts.sqlite").exists()
    ):
        raise ValueError("plan changed or campaign already started")
    journal = V5AttemptJournal(out / "attempts.sqlite")
    ledger = MemoryCalibrationLedger(Decimal(20), Decimal(0), journal=journal)
    transport = ReliableTransport(
        config,
        lifecycle_id=content_digest(plan),
        ledger=ledger,
        phase_spend_limit=Decimal(20),
        phase_wire_limit=actions * 2,
        phase_deadline=time.time() + 43200,
        request_bounder=full_context_bound,
    )
    rows = []
    started = time.monotonic()
    stop = "running"
    error = None

    def summary():
        return {
            "plan_digest": content_digest(plan),
            "model": config.model,
            "frozen_benchmark_revision": FROZEN,
            "assigned": len(jobs),
            "completed": len(rows),
            "retained_completed": 77,
            "unrun": len(jobs) - len(rows),
            "stop_reason": stop,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "results": rows,
            "counts": {
                m: dict(Counter(r["classification"] for r in rows if r["mode"] == m))
                for m in manifests
            },
            "spend": ledger.to_dict(),
            "maximum_spend_usd": "20.00",
            "journal_integrity": journal.integrity_report(),
        }

    write(out / "summary.json", summary())
    try:
        for job in jobs:
            policy = HaikuOpenRouterPolicy(config, retain_screenshots=job["mode"] == "history")
            runner = CliMemoryRunner(
                journal=journal,
                manifest=manifests[job["mode"]],
                policy=policy,
                transport=transport,
                approved_caps=caps,
                deadline_executor=CooldownExecutor(transport),
                time_exhausted=lambda: time.monotonic() - started >= 43000,
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
            write(out / "summary.json", summary())
            print(
                json.dumps(
                    {
                        "completed": len(rows),
                        "classification": row["classification"],
                        "spend": ledger.to_dict(),
                    }
                ),
                flush=True,
            )
            if result["classification"] not in terminal or ledger.blocked:
                stop = result["classification"]
                break
        else:
            stop = "completed_all_assignments"
    except BaseException as exc:
        stop = "execution_error"
        error = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        transport._retire("phase_closed")
        transport.wait_until_idle(10)
        write(out / "summary.json", summary())
        journal.close()


if __name__ == "__main__":
    main()
