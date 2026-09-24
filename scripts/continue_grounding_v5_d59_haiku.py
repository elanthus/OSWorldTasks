"""Continue a stopped r3 campaign under an explicit malformed-output amendment.

The predecessor is retained untouched. SQLite backups carry every reservation into
new journals; only the exact unrun suffix is dispatched. No model answer is repaired.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy, CliMemoryRunner
from pixelgym.grounding.v5.codex_cli_policy import SubscriptionExemptLedger
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import (
    APPROVED_CAPS,
    APPROVED_RUNTIME_HOURS,
    sha256_file,
    validate_assignments,
)
from pixelgym.grounding.v5.journal import TERMINAL_ATTEMPT_KINDS, V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.screenshot_memory import require_clean_tracked_worktree
from scripts import run_grounding_v5_d59_haiku_network_retry as frozen
from scripts.run_grounding_v5_d59_haiku import read_object, write_object

ROOT = Path(__file__).resolve().parents[1]
OWNER_STATEMENT = (
    "ok, lets count the malformed response as a failure, then continue with the rest of the run."
)
CONTINUE_OUTCOMES = frozenset({"success_termination", "step_limit_truncation", "invalid_output"})


def stopped_prefix(plan: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = validate_assignments(plan)
    rows = summary["results"]
    if (
        summary["execution_plan_digest"] != plan["execution_plan_digest"]
        or summary["assigned"] != len(jobs)
        or not 0 < len(rows) < len(jobs)
        or summary["completed"] != len(rows)
        or summary["attempted"] != len(rows)
        or summary["unrun"] != len(jobs) - len(rows)
        or summary["stop_reason"] != "invalid_output"
        or summary["error"] is not None
        or summary["subprocesses_closed"] is not True
        or summary["unresolved_invocations"] != 0
        or Decimal(summary["incremental_experiment_charge_usd"]) != 0
        or rows[-1]["classification"] != "invalid_output"
    ):
        raise ValueError("predecessor must be fully settled and stopped on malformed output")
    for job, row in zip(jobs, rows, strict=False):
        if any(row.get(key) != value for key, value in job.items()):
            raise ValueError("completed assignments must be the exact frozen prefix")
        if row["classification"] not in CONTINUE_OUTCOMES:
            raise ValueError("predecessor contains another failure requiring review")
    return jobs[len(rows) :]


def copy_database(source: Path, target: Path) -> None:
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old,
        closing(sqlite3.connect(target)) as new,
    ):
        old.backup(new)


def validate_journals(output: Path, summary: dict[str, Any]) -> SubscriptionExemptLedger:
    journal = V5AttemptJournal(output / "attempts.sqlite")
    invocations = claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    try:
        if journal.integrity_report() != summary["journal_integrity"]:
            raise ValueError("attempt journal differs from the stopped summary")
        if invocations.integrity_report() != summary["invocation_integrity"]:
            raise ValueError("invocation journal differs from the stopped summary")
        events = journal.events()
        rows = summary["results"]
        completed = [e.payload for e in events if e.kind == "d59_assignment_completed"]
        started = [e.trial_id for e in events if e.kind == "d59_assignment_started"]
        if completed != rows or started != [r["trial_id"] for r in rows]:
            raise ValueError("summary differs from authoritative assignment events")
        for row in rows:
            if row["classification"] == "invalid_output":
                sealed = [
                    e
                    for e in events
                    if e.trial_id == row["trial_id"] and e.kind == "sealed_unsuccessful_result"
                ]
                if not sealed or sealed[-1].payload.get("failure_code") != "parse_failure":
                    raise ValueError("invalid output must be an actual parse failure")
        attempts = [e for e in events if e.kind == "attempt_started"]
        attempt_ids = {(e.trial_id, e.step_index, e.attempt_index) for e in attempts}
        terminal_ids = {
            (e.trial_id, e.step_index, e.attempt_index)
            for e in events
            if e.kind in TERMINAL_ATTEMPT_KINDS
        }
        if attempt_ids != terminal_ids or len(attempt_ids) != len(attempts):
            raise ValueError("predecessor has an unfinished attempt")
        ledger = SubscriptionExemptLedger(Decimal("1.00"), Decimal("0.00"))
        keys = set()
        for event in attempts:
            key = event.payload["idempotency_key"]
            record = invocations.record(key)
            if (
                key in keys
                or record is None
                or record["status"] != "response"
                or record["outcome"] is None
                or record["outcome"]["transport_outcome"]["status"] != "response"
            ):
                raise ValueError("predecessor has an unsettled or duplicate invocation")
            keys.add(key)
            ledger.reserve(key)
            ledger.mark_process_started()
        if len(keys) != invocations.integrity_report()["invocation_count"]:
            raise ValueError("unaccounted invocation in predecessor")
        if ledger.processes_started != summary["provider_processes_started"]:
            raise ValueError("process accounting differs from predecessor")
        totals = {
            field: sum(int(row[field]) for row in rows)
            for field in (
                "environment_actions",
                "model_attempts",
                "provider_control_requests",
                "provider_wire_requests",
            )
        }
        if journal.call_counts() != (totals["model_attempts"], totals["provider_control_requests"]):
            raise ValueError("prior reservations differ from result accounting")
        if totals["provider_wire_requests"] != len(keys):
            raise ValueError("wire accounting differs from invocation journal")
        for field, cap in zip(totals, APPROVED_CAPS.to_dict().values(), strict=True):
            if totals[field] > cap:
                raise ValueError("predecessor exceeded an approved cap")
        return ledger
    finally:
        invocations.close()
        journal.close()


def inputs(output: Path) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    require_clean_tracked_worktree(ROOT)
    plan = read_object(output / "execution-plan.json")
    approval = read_object(output / "owner-approval.json")
    frozen.validate_plan(plan, approval)
    identity = claude.probe_claude_runtime()
    manifests = frozen.manifests(ROOT, plan, identity)
    binding = frozen.binding(ROOT, plan=plan, approval=approval, runtime_identity=identity)
    binding["policy_manifest_digests"] = {
        mode: content_digest(manifest.to_dict()) for mode, manifest in manifests.items()
    }
    if read_object(output / "execution-binding.json") != binding:
        raise ValueError("original runtime or policy binding changed")
    summary = read_object(output / "predecessor-summary.json")
    if content_digest(binding) != summary["execution_binding_digest"]:
        raise ValueError("predecessor summary has another runtime binding")
    return plan, summary, identity, manifests


def prepare(predecessor: Path, output: Path, owner_statement: str) -> None:
    if owner_statement != OWNER_STATEMENT:
        raise ValueError("this continuation requires the recorded owner direction")
    plan = read_object(predecessor / "execution-plan.json")
    summary = read_object(predecessor / "summary.json")
    remaining = stopped_prefix(plan, summary)
    launch = read_object(predecessor / "launch.json")
    started = datetime.fromisoformat(launch["started_at"])
    if started.utcoffset() is None:
        raise ValueError("original launch time must include timezone")
    deadline = started + timedelta(hours=APPROVED_RUNTIME_HOURS)
    if datetime.now(UTC) >= deadline:
        raise ValueError("the original approved runtime window has expired")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("execution-plan.json", "owner-approval.json", "execution-binding.json"):
        shutil.copyfile(predecessor / name, output / name)
    shutil.copyfile(predecessor / "summary.json", output / "predecessor-summary.json")
    for name in ("attempts.sqlite", "invocations.sqlite"):
        copy_database(predecessor / name, output / name)
    inputs(output)
    validate_journals(output, summary)
    amendment = {
        "schema_version": "pixelgym-d59-malformed-output-continuation-v1",
        "owner_statement": owner_statement,
        "recorded_at": datetime.now(UTC).isoformat(),
        "execution_plan_digest": plan["execution_plan_digest"],
        "predecessor_summary_digest": content_digest(summary),
        "retained_results": len(summary["results"]),
        "remaining_assignments": len(remaining),
        "next_trial_id": remaining[0]["trial_id"],
        "original_started_at": started.isoformat(),
        "deadline": deadline.isoformat(),
        "continue_outcomes": sorted(CONTINUE_OUTCOMES),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "continuation_source_sha256": sha256_file(Path(__file__)),
        "predecessor_directory": str(predecessor),
        "previous_results_reclassified": 0,
        "completed_assignments_replayed": 0,
        "provider_calls_during_preparation": 0,
    }
    write_object(output / "continuation-approval.json", amendment)
    print(
        json.dumps(
            {
                "retained": len(summary["results"]),
                "remaining": len(remaining),
                "next_trial_id": remaining[0]["trial_id"],
            }
        )
    )


def execute(output: Path) -> None:
    plan, previous, identity, manifests = inputs(output)
    jobs = stopped_prefix(plan, previous)
    amendment = read_object(output / "continuation-approval.json")
    if (
        amendment["owner_statement"] != OWNER_STATEMENT
        or amendment["execution_plan_digest"] != plan["execution_plan_digest"]
        or amendment["predecessor_summary_digest"] != content_digest(previous)
        or amendment["continue_outcomes"] != sorted(CONTINUE_OUTCOMES)
        or amendment["continuation_source_sha256"] != sha256_file(Path(__file__))
        or amendment["next_trial_id"] != jobs[0]["trial_id"]
    ):
        raise ValueError("continuation approval or source changed")
    started_at = datetime.fromisoformat(amendment["original_started_at"])
    deadline = started_at + timedelta(hours=APPROVED_RUNTIME_HOURS)
    if deadline.isoformat() != amendment["deadline"]:
        raise ValueError("continuation may not extend the original runtime window")
    seconds_remaining = (deadline - datetime.now(UTC)).total_seconds()
    if seconds_remaining <= 0:
        raise ValueError("the original approved runtime window has expired")
    ledger = validate_journals(output, previous)
    # Exclusive, persistent claim: never auto-restart after a partially executed continuation.
    with (output / "execution-started.json").open("x") as claim:
        json.dump({"continuation_digest": content_digest(amendment)}, claim)
    invocations = claude.ClaudeInvocationJournal(output / "invocations.sqlite")
    journal = V5AttemptJournal(output / "attempts.sqlite")
    transport = claude.ClaudeCodeTransport(
        ledger=ledger,
        invocation_journal=invocations,
        runtime_identity=identity,
        expected_resolved_model=claude.MODEL,
        allow_timeout_retry=True,
        api_retry_limit=0,
        allow_connection_retry=True,
    )
    total_assignments = previous["assigned"]
    rows = list(previous["results"])
    stop_reason = "running"
    error = None
    monotonic_deadline = time.monotonic() + seconds_remaining

    def expired(reserve: float = 0) -> bool:
        return time.monotonic() + reserve >= monotonic_deadline or (
            datetime.now(UTC) + timedelta(seconds=reserve) >= deadline
        )

    def summary() -> dict[str, Any]:
        return {
            **previous,
            "schema_version": "pixelgym-d59-continuation-summary-v1",
            "continuation_digest": content_digest(amendment),
            "continuation_source_revision": amendment["source_revision"],
            "attempted": sum(e.kind == "d59_assignment_started" for e in journal.events()),
            "completed": len(rows),
            "unrun": total_assignments
            - sum(e.kind == "d59_assignment_started" for e in journal.events()),
            "stop_reason": stop_reason,
            "error": error,
            "elapsed_seconds": (datetime.now(UTC) - started_at).total_seconds(),
            "elapsed_includes_pause": True,
            "results": rows,
            "counts": {
                phase: {
                    mode: dict(
                        Counter(
                            r["classification"]
                            for r in rows
                            if r["phase"] == phase and r["mode"] == mode
                        )
                    )
                    for mode in ("history", "stateless")
                }
                for phase in ("primary", "reliability")
            },
            "phase_usage": {
                phase: {
                    field: sum(int(r[field]) for r in rows if r["phase"] == phase)
                    for field in (
                        "environment_actions",
                        "model_attempts",
                        "provider_control_requests",
                        "provider_wire_requests",
                    )
                }
                for phase in ("primary", "reliability")
            },
            "provider_processes_started": ledger.processes_started,
            "incremental_experiment_charge_usd": str(ledger.incremental_experiment_charge_usd),
            "informational_list_price_equivalent_usd": str(
                Decimal(previous["informational_list_price_equivalent_usd"])
                + ledger.incremental_informational_list_price_equivalent_usd
            ),
            "unresolved_invocations": len(ledger.unresolved),
            "journal_integrity": journal.integrity_report(),
            "invocation_integrity": invocations.integrity_report(),
            "subprocesses_closed": transport.subprocesses_closed,
        }

    try:
        journal.append_event(
            event_key="d59-malformed-output-continuation",
            kind="owner_continuation_authorized",
            trial_id=jobs[0]["trial_id"],
            step_index=0,
            payload=amendment,
        )
        write_object(output / "summary.json", summary())
        for job in jobs:
            if expired():
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
                approved_caps=APPROVED_CAPS,
                time_exhausted=partial(expired, manifests[mode].request_deadline_seconds),
            )
            journal.append_event(
                event_key=job["trial_id"] + "/assignment_started",
                kind="d59_assignment_started",
                trial_id=job["trial_id"],
                step_index=0,
                payload={"job": job, "execution_plan_digest": plan["execution_plan_digest"]},
            )
            result = runner.run(
                trial_id=job["trial_id"],
                task=generate_memory_task(int(job["seed"])),
                backend=FocusMemoryBackend(),
            ).to_dict()
            row = {
                **job,
                **result,
                **episode_measurements(journal, job["trial_id"], int(job["seed"])),
            }
            journal.append_event(
                event_key=job["trial_id"] + "/assignment_completed",
                kind="d59_assignment_completed",
                trial_id=job["trial_id"],
                step_index=int(result["environment_actions"]),
                payload=row,
            )
            rows.append(row)
            write_object(output / "summary.json", summary())
            print(
                json.dumps(
                    {
                        "completed": len(rows),
                        "assigned": previous["assigned"],
                        "trial_id": job["trial_id"],
                        "classification": row["classification"],
                    }
                ),
                flush=True,
            )
            if row["classification"] not in CONTINUE_OUTCOMES or ledger.blocked:
                stop_reason = (
                    "invocation_ledger_blocked" if ledger.blocked else str(row["classification"])
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
            invocations.close()
            journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path)
    parser.add_argument("--owner-statement")
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.predecessor is None or args.owner_statement is None:
            parser.error("prepare requires --predecessor and --owner-statement")
        prepare(args.predecessor.resolve(), args.output.resolve(), args.owner_statement)
    else:
        execute(args.output.resolve())


if __name__ == "__main__":
    main()
