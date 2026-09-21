"""Publish and verify the response-free fresh Haiku CLI calibration evidence.

Export audits the private SQLite journals without invoking a provider. Verification
rebuilds the checked-in report only from the committed response-free snapshot.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

from pixelgym.grounding.v5.claude_code_policy import ClaudeInvocationJournal
from pixelgym.grounding.v5.contracts import CallCaps, content_digest, sha256_bytes
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from scripts.publish_pr196_calibration import ReadOnlyJournal, metrics
from scripts.run_grounding_v5_cli_memory import FROZEN_RUNTIME_FILES, validate_fresh_approval

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "artifacts/grounding-v5-haiku-cli-replication"
SOURCE_PLAN = ROOT / "artifacts/grounding-v5-d58-gemini38-calibration/execution-plan.json"
FROZEN = "1e5d9c0d19acf51505919deefe0d155c2ab22b26"
PLAN_DIGEST = "sha256:6b2fa2ccf8ae9f2d5c42666167116a2f6dc6ed00c6b39cce4c0d62bdbdadc476"
TERMINAL = {"success_termination", "step_limit_truncation", "invalid_output"}
SOURCE_FILES = (
    "pixelgym/grounding/v5/memory_generator.py",
    "pixelgym/grounding/v5/memory_backend.py",
    "pixelgym/grounding/v5/memory_focus_backend.py",
    "pixelgym/grounding/v5/screenshot_memory.py",
    "pixelgym/grounding/v5/claude_code_policy.py",
    "scripts/run_grounding_v5_cli_memory.py",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def encoded(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _assignment_identity(row: dict) -> tuple:
    return tuple(row[key] for key in ("seed", "mode", "task_id", "task_digest", "action_limit"))


def _source_hashes(revision: str) -> dict[str, str]:
    hashes = {}
    for path in SOURCE_FILES:
        source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
        hashes[path] = "sha256:" + sha256_bytes(source)
    return hashes


def audit(private_directory: Path) -> dict:
    """Audit the retained private evidence and return a response-free snapshot."""

    plan = read(private_directory / "execution-plan.json")
    summary = read(private_directory / "summary.json")
    require(content_digest(plan) == PLAN_DIGEST == summary["plan_digest"], "plan binding mismatch")
    require(plan["adapter_revision"] == summary["adapter_revision"], "revision mismatch")
    require(plan["frozen_benchmark_revision"] == summary["frozen_benchmark_revision"] == FROZEN, "benchmark revision mismatch")
    expected_conditions = dict(sorted(Counter(job["mode"] for job in plan["jobs"]).items()))
    require(plan["fresh_cohort"] == {"assignments": len(plan["jobs"]), "conditions": expected_conditions, "confirmatory_tasks_exposed": 0, "prior_outcomes_reused": 0}, "fresh-cohort contract mismatch")
    require(summary["completed"] == summary["assigned"] == 100 and summary["unrun"] == 0, "cohort is incomplete")
    require(summary["stop_reason"] == "completed_all_assignments" and summary["error"] is None, "cohort did not finish cleanly")
    require(summary["subprocesses_closed"] and summary["unresolved_invocations"] == 0, "provider process remains unresolved")

    approval = validate_fresh_approval(
        private_directory / "owner-approval.json",
        plan,
        CallCaps(**plan["caps"]),
    )

    source_plan = read(SOURCE_PLAN)
    require(content_digest(source_plan) == plan["source_plan_digest"], "source-plan digest mismatch")
    require(
        [_assignment_identity(row) for row in plan["jobs"]]
        == [_assignment_identity(row) for row in source_plan["jobs"]],
        "fresh panel differs from the PR196 calibration panel",
    )
    for path in FROZEN_RUNTIME_FILES:
        executed = subprocess.check_output(
            ["git", "show", f"{plan['adapter_revision']}:{path}"], cwd=ROOT
        )
        frozen = subprocess.check_output(["git", "show", f"{FROZEN}:{path}"], cwd=ROOT)
        require(executed == frozen, "runtime source differs from frozen benchmark: " + path)

    journal = ReadOnlyJournal(private_directory / "attempts.sqlite")
    try:
        integrity = journal.integrity_report()
        require(integrity == summary["journal_integrity"], "journal integrity mismatch")
        events = journal.events()
        results = [e.payload for e in events if e.kind == "cli_memory_assignment_completed"]
        require(results == summary["results"], "recorded results differ from summary")
        require(len(results) == 100, "result count mismatch")
        require(
            summary["counts"]
            == {
                mode: dict(Counter(row["classification"] for row in results if row["mode"] == mode))
                for mode in ("history", "stateless")
            },
            "summary classification counts differ",
        )
        jobs = {(row["seed"], row["mode"]): row for row in plan["jobs"]}
        require(len(jobs) == 100, "duplicate assignments")
        seen = set()
        for row in results:
            key = row["seed"], row["mode"]
            require(key in jobs and key not in seen, "duplicate or unknown result")
            seen.add(key)
            require(row["classification"] in TERMINAL, "nonterminal result retained")
            task = generate_memory_task(row["seed"])
            require(task.task_id == row["task_id"] == jobs[key]["task_id"], "task identity mismatch")
            require(content_digest(task.canonical_dict()) == row["task_digest"] == jobs[key]["task_digest"], "task digest mismatch")
            require(task.max_episode_steps == row["action_limit"] == jobs[key]["action_limit"], "action cap mismatch")
            measured = episode_measurements(journal, row["trial_id"], row["seed"])
            require(all(row[name] == value for name, value in measured.items()), "stored episode measurements differ")
            dispatches = [e for e in events if e.trial_id == row["trial_id"] and e.kind == "dispatch_committed"]
            require(len(dispatches) == row["environment_actions"], "action count mismatch")
            success = any(e.payload["terminated"] and e.payload["reward"] == 1 for e in dispatches)
            require(success == row["success"] == (row["classification"] == "success_termination"), "success disagrees with host dispatch")
            if row["classification"] == "step_limit_truncation":
                require(dispatches[-1].payload["truncated"] and len(dispatches) == row["action_limit"], "step limit mismatch")
    finally:
        journal.close()

    connection = sqlite3.connect(
        f"file:{(private_directory / 'invocations.sqlite').resolve()}?mode=ro", uri=True
    )
    try:
        invocation = object.__new__(ClaudeInvocationJournal)
        invocation._connection = connection
        invocation_integrity = invocation.integrity_report()
        require(invocation_integrity == summary["invocation_integrity"], "invocation integrity mismatch")
        statuses = dict(connection.execute("SELECT status, COUNT(*) FROM invocations GROUP BY status"))
        require(statuses == {"response": 2416}, "unexpected invocation status")
    finally:
        connection.close()

    totals = {
        "environment_actions": sum(row["environment_actions"] for row in results),
        "model_attempts": sum(row["model_attempts"] for row in results),
        "provider_control_requests": sum(row["provider_control_requests"] for row in results),
        "provider_wire_requests": sum(row["provider_wire_requests"] for row in results),
    }
    caps = plan["caps"]
    require(totals["environment_actions"] <= caps["environment_action_cap"], "environment-action cap exceeded")
    require(totals["model_attempts"] <= caps["model_attempt_cap"], "model-attempt cap exceeded")
    require(totals["provider_control_requests"] <= caps["provider_control_request_cap"], "provider-control cap exceeded")
    require(totals["provider_wire_requests"] <= caps["provider_wire_request_cap"], "provider-wire cap exceeded")

    snapshot = {
        "schema_version": "pixelgym-pr196-haiku-cli-replication-snapshot-v1",
        "plan_digest": PLAN_DIGEST,
        "approval_digest": content_digest(approval),
        "summary_digest": content_digest(summary),
        "adapter_revision": plan["adapter_revision"],
        "frozen_benchmark_revision": FROZEN,
        "source_plan": "artifacts/grounding-v5-d58-gemini38-calibration/execution-plan.json",
        "source_plan_digest": plan["source_plan_digest"],
        "source_hashes": _source_hashes(plan["adapter_revision"]),
        "runtime_identity": plan["runtime_identity"],
        "policy_manifests": plan["policy_manifests"],
        "caps": caps,
        "maximum_elapsed_seconds": plan["maximum_elapsed_seconds"],
        "transport_retries": plan["transport_retries"],
        "fresh_cohort": plan["fresh_cohort"],
        "assigned": summary["assigned"],
        "completed": summary["completed"],
        "unrun": summary["unrun"],
        "stop_reason": summary["stop_reason"],
        "elapsed_seconds": summary["elapsed_seconds"],
        "incremental_experiment_charge_usd": summary["incremental_experiment_charge_usd"],
        "counts": summary["counts"],
        "jobs": plan["jobs"],
        "results": results,
        "accounting": {
            **totals,
            "provider_processes_started": summary["provider_processes_started"],
            "invocation_statuses": statuses,
            "journal_integrity": integrity,
            "invocation_integrity_digest": content_digest(invocation_integrity),
            "unresolved_invocations": summary["unresolved_invocations"],
            "subprocesses_closed": summary["subprocesses_closed"],
        },
        "private_audit": {
            "provider_calls_made": 0,
            "read_only": True,
            "journal_integrity_matches": True,
            "invocation_integrity_matches": True,
            "host_success_and_measurements_match": True,
            "caps_respected": True,
            "confirmatory_tasks_exposed": 0,
        },
    }
    text = encoded(snapshot)
    require("/Users/" not in text and "/private/" not in text and "data:image/" not in text, "private surface in snapshot")
    return snapshot


def paired(rows: list[dict]) -> dict:
    by_seed: dict[int, dict[str, dict]] = {}
    seen = set()
    for row in rows:
        key = row["seed"], row["mode"]
        require(key not in seen, "unpaired results")
        seen.add(key)
        by_seed.setdefault(row["seed"], {})[row["mode"]] = row
    require(
        len(rows) == 100
        and len(by_seed) == 50
        and all(set(pair) == {"history", "stateless"} for pair in by_seed.values()),
        "unpaired results",
    )
    outcomes = Counter()
    for pair in by_seed.values():
        history = pair["history"]["success"]
        stateless = pair["stateless"]["success"]
        label = "both" if history and stateless else "history_only" if history else "stateless_only" if stateless else "neither"
        outcomes[label] += 1
    return {
        "pairs": len(by_seed),
        "outcomes": {name: outcomes[name] for name in ("both", "history_only", "stateless_only", "neither")},
    }


def build(directory: Path = DIRECTORY) -> tuple[dict, str]:
    sources = read(directory / "sources.json")
    raw = (directory / "snapshot.json").read_bytes()
    require("sha256:" + sha256_bytes(raw) == sources["snapshot_sha256"], "snapshot digest mismatch")
    snapshot = json.loads(raw)
    require(snapshot["plan_digest"] == PLAN_DIGEST and snapshot["frozen_benchmark_revision"] == FROZEN, "snapshot identity mismatch")
    require(content_digest(read(SOURCE_PLAN)) == snapshot["source_plan_digest"], "source-plan binding mismatch")
    require(snapshot["completed"] == 100 and snapshot["unrun"] == 0, "snapshot is incomplete")
    rows = snapshot["results"]
    conditions = {mode: metrics([row for row in rows if row["mode"] == mode]) for mode in ("history", "stateless")}
    result = {
        "schema_version": "pixelgym-pr196-haiku-cli-replication-report-v1",
        "provider_calls_made": 0,
        "snapshot_sha256": sources["snapshot_sha256"],
        "plan_digest": snapshot["plan_digest"],
        "adapter_revision": snapshot["adapter_revision"],
        "frozen_benchmark_revision": snapshot["frozen_benchmark_revision"],
        "runtime_identity": snapshot["runtime_identity"],
        "conditions": conditions,
        "paired": paired(rows),
        "accounting": snapshot["accounting"],
        "elapsed_seconds": snapshot["elapsed_seconds"],
        "incremental_experiment_charge_usd": snapshot["incremental_experiment_charge_usd"],
        "caps": snapshot["caps"],
    }
    return result, render(result)


def render(data: dict) -> str:
    lines = [
        "# Fresh Haiku Claude Code CLI calibration",
        "",
        "The fresh matched calibration completed all 100 assignments: 50 screenshot-history and 50 stateless episodes on the PR196 calibration task panel. It used the exact `claude-haiku-4-5-20251001` model through Claude Code CLI 2.1.267 with default reasoning effort. These are calibration observations, not confirmatory results, a model ranking, or a human milestone verdict.",
        "",
        "## Outcomes",
        "",
        "| Condition | Episodes | Successes | Step limits | Invalid outputs |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode in ("history", "stateless"):
        row = data["conditions"][mode]
        lines.append(f"| {mode} | {row['episodes']} | {row['successes']} | {row['classifications'].get('step_limit_truncation', 0)} | {row['classifications'].get('invalid_output', 0)} |")
    paired_outcomes = data["paired"]["outcomes"]
    lines += [
        "",
        "Every assignment contributes its first terminal outcome. Invalid outputs remain failures; none was discarded or rescored. The matched pairs comprise 0 both-success, 28 history-only, 5 stateless-only, and 17 neither-success outcomes.",
        "",
        "## Memory observations",
        "",
        "| Condition | Reached both consumers | Correct first choices / attempted | Valid first choices | Actions | Model attempts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    require(paired_outcomes == {"both": 0, "history_only": 28, "stateless_only": 5, "neither": 17}, "unexpected paired outcome")
    for mode in ("history", "stateless"):
        row = data["conditions"][mode]
        lines.append(f"| {mode} | {row['reached_both_consumers']}/{row['episodes']} | {row['first_choices_correct']}/{row['first_choices_attempted']} | {row['first_choices_valid']} | {row['actions']} | {row['model_attempts']} |")
    accounting = data["accounting"]
    caps = data["caps"]
    lines += [
        "",
        "First-choice denominators count observed consumer-choice attempts. Episodes that never reached a consumer remain in the episode denominator but cannot contribute a choice observation. Measurements come from stored host checkpoints and committed dispatches, not model self-reports.",
        "",
        "## Execution and caps",
        "",
        f"The run used {accounting['environment_actions']} of {caps['environment_action_cap']} allowed environment actions and {accounting['model_attempts']} of {caps['model_attempt_cap']} allowed model attempts. It recorded {accounting['provider_wire_requests']} of {caps['provider_wire_request_cap']} allowed CLI wire requests and {accounting['provider_control_requests']} of {caps['provider_control_request_cap']} provider-control requests. All {accounting['provider_processes_started']} started CLI processes have terminal response records; unresolved invocations are zero and subprocess closure is recorded.",
        "",
        f"Elapsed execution time was {data['elapsed_seconds'] / 3600:.2f} hours within the approved 12-hour window. Incremental experiment charge is recorded as USD {data['incremental_experiment_charge_usd']} under the existing Max subscription; that excludes the subscription fee and is not a zero inference-cost claim.",
        "",
        "## Provenance and limits",
        "",
        f"The execution plan digest is `{data['plan_digest']}` and the adapter revision is `{data['adapter_revision']}`. The task identities exactly match the checked-in PR196 calibration plan and preserve its frozen benchmark revision `{data['frozen_benchmark_revision']}`. The fresh cohort reused no prior outcome and exposed no confirmatory task.",
        "",
        "The private audit opened both SQLite journals read-only, verified every stored-object and event-chain digest, matched all result events to the sealed summary, recomputed episode measurements, checked success against privileged host dispatches, verified every invocation receipt, and checked the exact caps. It made zero provider calls and did not replay an episode or reinterpret an invalid output.",
        "",
        "The checked-in snapshot excludes raw provider responses, prompts, screenshots, checkpoint contents, credentials, private paths, and process IDs. Public verification checks its hash, task-panel binding, response-free derivation, and deterministic report bytes; it cannot repeat the private-journal audit without the retained local journals.",
        "",
        "```sh",
        ".venv/bin/python -m scripts.publish_haiku_cli_replication --verify",
        "```",
        "",
        "The earlier consolidated PR196 Haiku result combined interrupted and successor CLI/API cohorts. This fresh result uses one Claude Code route and should remain separately identified. No D5.9 execution is authorized, and no human gate is declared here.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    require(not (args.export and args.verify), "export and verify are separate operations")
    if args.export:
        snapshot = audit(args.export)
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        snapshot_text = encoded(snapshot)
        snapshot_path = DIRECTORY / "snapshot.json"
        if snapshot_path.exists():
            require(snapshot_path.read_text() == snapshot_text, "refuse to overwrite changed snapshot")
        else:
            snapshot_path.write_text(snapshot_text)
        (DIRECTORY / "sources.json").write_text(encoded({"snapshot_sha256": "sha256:" + sha256_bytes(snapshot_text.encode()), "plan_digest": PLAN_DIGEST, "provider_calls_made": 0}))
    result, report = build()
    for name, text in {"results.json": encoded(result), "report.md": report}.items():
        path = DIRECTORY / name
        if args.verify:
            require(path.read_text() == text, "generated artifact differs: " + name)
        else:
            path.write_text(text)
    print(json.dumps({"verified": True, "episodes": 100, "provider_calls_made": 0}))


if __name__ == "__main__":
    main()
