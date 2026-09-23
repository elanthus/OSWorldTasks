"""Audit the stopped D5.9 run and build its no-call API-retry successor."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_retry_successor import (
    OUTPUT_DIRECTORY,
    PREDECESSOR_PLAN_DIGEST,
    expected_outputs,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / OUTPUT_DIRECTORY


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def audit_discarded_run(evidence: Path) -> dict[str, Any]:
    summary_path = evidence / "summary.json"
    attempts_path = evidence / "attempts.sqlite"
    invocations_path = evidence / "invocations.sqlite"
    binding_path = evidence / "execution-binding.json"
    summary = _read(summary_path)
    if (
        summary.get("execution_plan_digest") != PREDECESSOR_PLAN_DIGEST
        or summary.get("stop_reason") != "policy_violation"
        or summary.get("error") is not None
        or summary.get("assigned") != 432
        or summary.get("attempted") != 1
        or summary.get("completed") != 1
        or summary.get("unrun") != 431
        or summary.get("subprocesses_closed") is not True
        or summary.get("unresolved_invocations") != 0
    ):
        raise ValueError("stopped-run summary differs from the reviewed failure")
    results = summary.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("stopped run must retain exactly one terminal result")
    result = results[0]
    if (
        result.get("trial_id") != "d59-haiku-primary-6000-history-r0"
        or result.get("classification") != "policy_violation"
        or result.get("environment_actions") != 17
        or result.get("model_attempts") != 18
        or result.get("provider_control_requests") != 0
        or result.get("provider_wire_requests") != 18
    ):
        raise ValueError("stopped-run result differs from the reviewed failure")

    with tempfile.TemporaryDirectory(prefix="pixelgym-d59-discarded-audit-") as temporary:
        audit_copy = Path(temporary) / "attempts.sqlite"
        shutil.copy2(attempts_path, audit_copy)
        journal = V5AttemptJournal(audit_copy)
        try:
            journal_integrity = journal.integrity_report()
        finally:
            journal.close()
    if journal_integrity != summary["journal_integrity"]:
        raise ValueError("stopped-run attempt journal changed")

    with sqlite3.connect(
        f"file:{invocations_path.resolve()}?mode=ro&immutable=1", uri=True
    ) as connection:
        status_counts = {
            str(status): int(count)
            for status, count in connection.execute(
                "SELECT status, COUNT(*) FROM invocations GROUP BY status"
            )
        }
        rows = connection.execute(
            "SELECT raw_stdout, outcome FROM invocations WHERE status = 'policy_violation'"
        ).fetchall()
    if status_counts != {"policy_violation": 1, "response": 17} or len(rows) != 1:
        raise ValueError("stopped-run invocation statuses changed")
    stdout = bytes(rows[0][0]).decode("utf-8")
    outcome = json.loads(bytes(rows[0][1]))
    violation = outcome.get("policy_violation")
    if violation != "unauthorized_system_event:api_retry":
        raise ValueError("stopped-run violation changed")
    retry_events = [
        event
        for line in stdout.splitlines()
        if isinstance((event := json.loads(line)), dict)
        and event.get("type") == "system"
        and event.get("subtype") == "api_retry"
    ]
    if [event.get("attempt") for event in retry_events] != [1, 2] or any(
        event.get("max_retries") != 10 for event in retry_events
    ):
        raise ValueError("stopped-run API retry telemetry changed")
    receipt = {
        "schema_version": "pixelgym-agent-v5-d59-haiku-discarded-run-v1",
        "recorded_at": "2026-09-23",
        "execution_plan_digest": PREDECESSOR_PLAN_DIGEST,
        "disposition": "discarded_from_scoring",
        "reason": "invalid_transport_policy_hidden_cli_api_retries",
        "restart_or_replay_performed": False,
        "provider_response_content_retained": False,
        "retained_terminal_assignment": {
            "trial_id": result["trial_id"],
            "phase": result["phase"],
            "seed": result["seed"],
            "mode": result["mode"],
            "repeat": result["repeat"],
            "classification": result["classification"],
            "environment_actions": result["environment_actions"],
            "final_stage_index": result["final_stage_index"],
        },
        "cohort_state": {"assigned": 432, "attempted": 1, "completed": 1, "unrun": 431},
        "observed_usage": {
            "runner_model_attempts": 18,
            "runner_provider_wire_requests": 18,
            "provider_control_requests": 0,
            "cli_internal_api_retry_events": len(retry_events),
            "minimum_provider_api_attempts": 18 + len(retry_events),
            "unresolved_invocations": 0,
        },
        "policy_violation": {
            "code": violation,
            "api_retry_attempts": [event["attempt"] for event in retry_events],
            "advertised_cli_max_retries": 10,
            "error_statuses": [event.get("error_status") for event in retry_events],
            "errors": [event.get("error") for event in retry_events],
        },
        "integrity": {
            "journal": journal_integrity,
            "summary_digest": content_digest(summary),
            "files": {
                "summary.json": _sha256(summary_path),
                "attempts.sqlite": _sha256(attempts_path),
                "invocations.sqlite": _sha256(invocations_path),
                "execution-binding.json": _sha256(binding_path),
            },
        },
        "limitations": [
            "The predecessor result is invalid infrastructure evidence and contributes no benchmark score.",
            "The old runner counted CLI processes, not the hidden API retries inside a process; minimum provider API attempts are therefore reported separately.",
            "Raw prompts, screenshots, provider responses, session identifiers, process IDs, and private paths are excluded from this public receipt.",
        ],
        "human_gate": "D5.10_not_evaluated_human_owned",
    }
    return {**receipt, "receipt_digest": content_digest(receipt)}


def write_outputs(outputs: dict[str, bytes], *, verify: bool) -> None:
    if verify:
        for name, payload in outputs.items():
            if (PUBLIC / name).read_bytes() != payload:
                raise SystemExit(f"checked-in successor artifact differs: {name}")
        return
    if PUBLIC.exists() and any(PUBLIC.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty directory: {PUBLIC}")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (PUBLIC / name).write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--private-evidence", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        if args.private_evidence is not None:
            raise SystemExit("--verify reads only the checked-in response-free receipt")
        discarded = _read(PUBLIC / "discarded-run.json")
    else:
        if args.private_evidence is None:
            raise SystemExit("initial generation requires --private-evidence")
        discarded = audit_discarded_run(args.private_evidence.resolve())
    outputs = expected_outputs(
        ROOT,
        source_revision=args.source_revision,
        discarded_run=discarded,
    )
    write_outputs(outputs, verify=args.verify)
    plan = json.loads(outputs["execution-plan.json"])
    print(
        json.dumps(
            {
                "execution_plan_digest": plan["execution_plan_digest"],
                "history_policy_id": plan["policy_manifests"]["history"]["policy_id"],
                "stateless_policy_id": plan["policy_manifests"]["stateless"]["policy_id"],
                "provider_calls_made": 0,
                "execution_enabled": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
