"""Audit retained D5.9 replication evidence read-only and export response-free results.

No CLI policy, authentication probe, model, or environment is launched. Archive
verification hashes both retained copies; it does not restore or remove evidence.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.claude_code_policy import ClaudeInvocationJournal
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from pixelgym.grounding.v5.d59_replication import phase_jobs, validate_approval
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.retention import verify_archive
from scripts.publish_pr196_calibration import ReadOnlyJournal
from scripts.report_grounding_v5_d59_haiku import ROW_FIELDS, counts

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/grounding-v5-d59-replication-results"
PLAN = ROOT / "artifacts/grounding-v5-d59-replication/execution-plan.json"
TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MEASUREMENTS = (
    "reached_consumers",
    "reached_both_consumers",
    "first_attempts",
    "final_stage_index",
    "model_attempts",
    "environment_actions_dispatched",
    "environment_dispatch_reservations",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def validate_rows(summary: dict, jobs: list[dict]) -> None:
    """Require every planned assignment once; no success/failure exclusions."""
    rows = summary["results"]
    expected = {job["trial_id"]: job for job in jobs}
    require(len(expected) == len(jobs), "duplicate planned trial")
    require(
        len(rows)
        == len(jobs)
        == summary["assigned"]
        == summary["attempted"]
        == summary["completed"]
        and len({row["trial_id"] for row in rows}) == len(rows)
        and {row["trial_id"] for row in rows} == set(expected),
        "missing, duplicate, or unplanned assignment",
    )
    require(
        summary["unrun"] == 0
        and summary["error"] is None
        and summary["subprocesses_closed"] is True
        and summary["stop_reason"] == "completed_all_assignments",
        "campaign is not consistently complete",
    )
    for row in rows:
        job = expected[row["trial_id"]]
        require(
            all(row[key] == value for key, value in job.items()), "assignment identity mismatch"
        )
        require(
            row["success"] == (row["classification"] == "success_termination"),
            "outcome flag mismatch",
        )
        for field in (
            "environment_actions",
            "model_attempts",
            "provider_wire_requests",
            "provider_control_requests",
        ):
            require(type(row[field]) is int and row[field] >= 0, "invalid resource count")
        require(
            row["environment_actions"] <= row["action_limit"]
            and row["model_attempts"] == row["provider_wire_requests"]
            and row["provider_control_requests"] == 0,
            "per-assignment cap mismatch",
        )
    require(counts(rows) == summary["counts"], "summary counts mismatch")


def audit_timeout(status: str, outcome: dict, event: Any, events: list[Any]) -> dict:
    """Only the frozen confirmed-stopped timeout exception can remain unresolved."""
    fault = outcome.get("cli_fault", {})
    require(
        status == "timeout"
        and outcome.get("process_confirmed_stopped") is True
        and fault.get("code") == "claude_process_timeout"
        and fault.get("phase") == "post_send"
        and fault.get("cost_knowledge") == "unknown"
        and fault.get("model_attempt_consumption") == "unknown",
        "unresolved invocation is not a confirmed-stopped unknown timeout",
    )
    matching = [e for e in events if e.step_index == event.step_index]
    retries = [e for e in matching if e.kind == "attempt_started"]
    require(
        sorted(e.attempt_index for e in retries) == [0, 1] and event.attempt_index == 0,
        "timeout lacks its single counted retry",
    )
    retry = next(e for e in retries if e.attempt_index == 1)
    permitted = [e for e in matching if e.kind == "retryable_transport_fault"]
    unknown = [e for e in matching if e.kind == "unknown_outcome_infrastructure_failure"]
    completed = [e for e in matching if e.kind == "attempt_completed" and e.attempt_index == 1]
    require(
        len(permitted) == len(unknown) == len(completed) == 1
        and permitted[0].payload["next_attempt_permitted"] is True
        and permitted[0].payload["bounded_retry_budget"] == 1
        and retry.payload["request_digest"] == event.payload["request_digest"],
        "timeout retry lineage mismatch",
    )
    return {
        "trial_id": event.trial_id,
        "step_index": event.step_index,
        "attempt_index": event.attempt_index,
        "status": status,
        "process_confirmed_stopped": True,
        "provider_completion": "unknown",
        "provider_usage": "unknown",
        "counted_attempts_for_action": 2,
        "retry_request_digest_matches": True,
        "invocation_key_digest": content_digest(event.payload["idempotency_key"]),
    }


def audit(directory: Path, archive: Path, backup: Path, *, root: Path = ROOT) -> dict:
    plan, summary = read(directory / "execution-plan.json"), read(directory / "summary.json")
    binding = read(directory / "execution-live-binding.json")
    approval = read(directory / "owner-approval.json")
    phase = summary["phase"]
    validate_approval(plan, approval, phase=phase)
    require(plan == read(root / PLAN.relative_to(ROOT)), "private/public plan mismatch")
    require(
        summary["execution_plan_digest"] == plan["execution_plan_digest"], "summary plan mismatch"
    )
    require(
        binding["execution_plan_digest"] == content_digest(plan)
        and binding["owner_approval_digest"] == content_digest(approval)
        and summary["execution_binding_digest"] == content_digest(binding),
        "binding mismatch",
    )
    require(binding["runtime_identity"] == plan["runtime_identity"], "runtime identity mismatch")
    require(binding["approved_caps"] == approval["approved_caps"], "bound cap mismatch")
    require(binding["source_files"] == plan["source_binding"]["files"], "bound source mismatch")
    require(
        all(sha256_file(root / path) == digest for path, digest in binding["source_files"].items()),
        "source file hash mismatch",
    )
    require(
        summary["source_revision"] == plan["source_binding"]["source_revision"]
        and summary["stop_rule_version"] == plan["stop_rule"]["version"],
        "protocol mismatch",
    )
    proof = binding["subscription_verification"]
    require(
        binding["sandbox_probe"]["passed"] is True
        and proof["http_status"] == 200
        and proof["organization_type"] == "claude_max"
        and proof["same_authentication_as_cli"] is True
        and proof["model_requests"] == 0,
        "sandbox or same-credential Max proof mismatch",
    )
    validate_rows(summary, phase_jobs(plan, phase))
    journal = ReadOnlyJournal(directory / "attempts.sqlite")
    try:
        integrity = journal.integrity_report()  # Streaming object hashes, bounded memory.
        require(integrity == summary["journal_integrity"], "attempt journal integrity mismatch")
        events = journal.events()
        by_trial: dict[str, list] = {}
        starts: dict[str, Any] = {}
        for event in events:
            by_trial.setdefault(event.trial_id, []).append(event)
            if event.kind == "attempt_started":
                key = event.payload["idempotency_key"]
                require(key not in starts, "duplicate attempt key")
                starts[key] = event
        rows = []
        identity = plan["runtime_identity"]
        for row in summary["results"]:
            trial = row["trial_id"]
            measured = episode_measurements(journal, trial, row["seed"])
            require(all(row[key] == measured[key] for key in MEASUREMENTS), "measurement mismatch")
            recorded = [
                e for e in by_trial[trial] if e.kind == "d59_replication_assignment_completed"
            ]
            require(
                len(recorded) == 1 and all(row[k] == v for k, v in recorded[0].payload.items()),
                "sealed row mismatch",
            )
            dispatch = [e for e in by_trial[trial] if e.kind == "dispatch_committed"]
            require(len(dispatch) == row["environment_actions"], "dispatch count mismatch")
            require(
                any(e.payload["terminated"] and e.payload["reward"] == 1 for e in dispatch)
                == row["success"],
                "host success mismatch",
            )
            if row["classification"] == "step_limit_truncation":
                require(
                    len(dispatch) == row["action_limit"] and dispatch[-1].payload["truncated"],
                    "step limit mismatch",
                )
            completed = [e for e in by_trial[trial] if e.kind == "attempt_completed"]
            tokens = Counter()
            for event in completed:
                usage = event.payload["usage"]
                require(
                    usage["resolved_model"] == identity["requested_model"]
                    and usage["auth_method"] == identity["auth_method"]
                    and usage["subscription_type"] == identity["subscription_type"]
                    and usage["cli_version"] == identity["cli_version"]
                    and usage["model_reasoning_effort"] == identity["reasoning_effort"]
                    and usage["policy_violation"] == "none",
                    "response identity mismatch",
                )
                require(
                    all(type(usage[k]) is int and usage[k] >= 0 for k in TOKENS),
                    "invalid token telemetry",
                )
                tokens.update({k: usage[k] for k in TOKENS})
            rows.append(
                {
                    **{k: row[k] for k in ROW_FIELDS},
                    **{k: measured[k] for k in MEASUREMENTS},
                    "reported_tokens": {k: tokens[k] for k in TOKENS},
                    "attempts_with_reported_usage": len(completed),
                }
            )
        unresolved = []
        invocation_reports, statuses = [], []
        seen = set()
        for worker in range(summary["workers"]):
            path = directory / f"invocations-{worker}.sqlite"
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                invocation = object.__new__(ClaudeInvocationJournal)
                invocation._connection = connection
                invocation_reports.append(invocation.integrity_report())
                statuses.append(
                    dict(
                        connection.execute(
                            "SELECT status, COUNT(*) FROM invocations GROUP BY status"
                        )
                    )
                )
                for key, status, request, outcome_bytes in connection.execute(
                    "SELECT idempotency_key,status,request_digest,outcome FROM invocations"
                ):
                    require(key in starts and key not in seen, "invocation/attempt join mismatch")
                    require(
                        request == starts[key].payload["request_digest"], "request binding mismatch"
                    )
                    seen.add(key)
                    outcome = json.loads(outcome_bytes)
                    enforcement = outcome["runtime_enforcement"]
                    require(
                        all(
                            enforcement[k] is True
                            for k in (
                                "os_sandbox_applied",
                                "environment_allowlist_applied",
                                "cli_restrictions_applied",
                            )
                        )
                        and enforcement["profile_template_digest"]
                        == binding["sandbox_probe"]["profile_template_digest"]
                        and outcome["egress_attempts"]["refused_count"] == 0,
                        "invocation enforcement mismatch",
                    )
                    if status != "response":
                        event = starts[key]
                        unresolved.append(
                            audit_timeout(status, outcome, event, by_trial[event.trial_id])
                        )
            finally:
                connection.close()
        require(seen == set(starts), "attempt missing invocation evidence")
        require(
            invocation_reports == summary["invocation_integrity"], "invocation integrity mismatch"
        )
        require(
            len(unresolved) == summary["unresolved_invocations"], "unknown outcome count mismatch"
        )
        require(
            sum(r["attempts_with_reported_usage"] for r in rows) + len(unresolved) == len(starts),
            "usage coverage mismatch",
        )
        authentication = [
            e.payload for e in events if e.kind == "d59_replication_authentication_recovery"
        ]
        require(all(e["model_requests"] == 0 for e in authentication), "login used model requests")
        require(
            all(
                e["active_attempts"] == 0
                for e in authentication
                if e["event"] == "auth_pause_started"
            ),
            "login pause did not drain",
        )
        require(
            round(
                sum(
                    e["excluded_wait_seconds"]
                    for e in authentication
                    if e["event"] == "auth_resumed"
                ),
                3,
            )
            == summary["authentication_wait_excluded_seconds"],
            "paused runtime mismatch",
        )
    finally:
        journal.close()
    usage = {
        key: sum(row[key] for row in rows)
        for key in (
            "environment_actions",
            "model_attempts",
            "provider_wire_requests",
            "provider_control_requests",
        )
    }
    caps = approval["approved_caps"]
    for key, cap_key in (
        ("environment_actions", "environment_action_cap"),
        ("model_attempts", "model_attempt_cap"),
        ("provider_wire_requests", "provider_wire_request_cap"),
        ("provider_control_requests", "provider_control_request_cap"),
    ):
        require(0 <= usage[key] <= caps[cap_key], "approved cap exceeded")
    require(
        usage["model_attempts"]
        == usage["provider_wire_requests"]
        == len(starts)
        == summary["provider_processes_started"],
        "attempt accounting mismatch",
    )
    for name in ("primary", "reliability"):
        require(
            {k: sum(r[k] for r in rows if r["phase"] == name) for k in usage}
            == summary["phase_usage"][name],
            "phase accounting mismatch",
        )
    require(
        summary["elapsed_seconds"]
        <= summary["approved_runtime_seconds"]
        == approval["approved_runtime_window_hours"] * 3600,
        "runtime cap mismatch",
    )
    receipt = summary["retention"]
    retained = {
        "archive": verify_archive(archive, expected_digest=receipt["manifest_digest"]),
        "backup": verify_archive(backup, expected_digest=receipt["manifest_digest"]),
    }
    require(
        retained["archive"] == receipt["archive"] and retained["backup"] == receipt["backup"],
        "retention receipt mismatch",
    )
    source_names = (
        "summary.json",
        "execution-plan.json",
        "owner-approval.json",
        "execution-binding.json",
        "execution-live-binding.json",
    )
    result = {
        "schema_version": "pixelgym-d59-replication-public-results-v1",
        "phase": phase,
        "execution_plan_digest": plan["execution_plan_digest"],
        "source_revision": summary["source_revision"],
        "runtime_identity": identity,
        "policy_ids": {m: p["policy_id"] for m, p in plan["policy_manifests"].items()},
        **{
            k: summary[k]
            for k in (
                "assigned",
                "completed",
                "unrun",
                "stop_reason",
                "phase_usage",
                "provider_processes_started",
                "unresolved_invocations",
                "subprocesses_closed",
                "elapsed_seconds",
                "wall_elapsed_seconds",
                "authentication_wait_excluded_seconds",
                "authentication_reads_during_execution",
                "incremental_experiment_charge_usd",
            )
        },
        "counts": counts(rows),
        "approved_caps": caps,
        "approved_runtime_window_hours": approval["approved_runtime_window_hours"],
        "journal_integrity": integrity,
        "invocation_integrity_digest": content_digest(invocation_reports),
        "invocation_statuses_by_worker": statuses,
        "unresolved_timeouts": unresolved,
        "retention": retained,
        "source_artifacts": {n: sha256_file(directory / n) for n in source_names},
        "audit": {
            "read_only": True,
            "model_calls": 0,
            "checks": [
                "exact plan and approval",
                "source hashes",
                "same-credential Max proof",
                "sandbox on every invocation",
                "journal objects and event chain",
                "invocation integrity",
                "every planned assignment",
                "host success and stored measurements",
                "caps and runtime",
                "counted timeout retry",
                "both archive hashes and SQLite integrity_check",
            ],
            "restore_drill_performed": False,
            "copies_on_same_local_disk": True,
        },
        "results": sorted(rows, key=lambda row: row["trial_id"]),
        "human_gate": {"D5.10": None},
    }
    validate_credential_free(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="run root containing pilot and full"
    )
    parser.add_argument(
        "--archive", type=Path, required=True, help="archive root containing both phases"
    )
    parser.add_argument(
        "--backup", type=Path, required=True, help="backup root containing both phases"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    reports = {
        phase: audit(args.input / phase, args.archive / phase, args.backup / phase)
        for phase in ("pilot", "full")
    }
    for phase, report in reports.items():
        data = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        path = args.output / ("report.json" if phase == "full" else "pilot-report.json")
        if args.verify:
            require(path.read_bytes() == data, "stored report differs")
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print(
        json.dumps(
            {
                p: {"assignments": r["completed"], "digest": content_digest(r)}
                for p, r in reports.items()
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
