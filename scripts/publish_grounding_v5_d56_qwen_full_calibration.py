#!/usr/bin/env python3
"""Audit and publish the frozen Qwen3-VL calibration without provider calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import REDACTION_POLICY_VERSION, content_digest
from pixelgym.grounding.v5.d56_calibration import _historical_calibration_manifest
from pixelgym.grounding.v5.d56_spend import (
    legacy_campaign_spend_disclosure,
    legacy_summary_spend_disclosure,
)
from pixelgym.grounding.v5.evidence import validate_credential_free

try:
    from scripts.audit_grounding_v5_d56_gemini_full_calibration import (
        IntegrityAuditError,
        _audit_journal,
        _file_record,
    )
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
    from audit_grounding_v5_d56_gemini_full_calibration import (  # type: ignore[no-redef]
        IntegrityAuditError,
        _audit_journal,
        _file_record,
    )

APPROVED_PLAN_SHA256 = "sha256:fc1f41d00df8c847d55765ea6af6b4c688463a893281f995f3eee3b770c43f7c"
PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-result-v1"
AUDIT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-integrity-audit-v1"
DERIVATIVE_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-calibration-publishable-v2"
RELATION_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-publication-relation-v1"
REPORT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-calibration-report-v1"
LATENCY_METHOD = "linear-interpolation-over-completed-response-latencies-v1"


class PublicationError(RuntimeError):
    """Stored evidence cannot produce the requested publication artifacts."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PublicationError(f"expected a JSON object: {path}")
    return value


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _relative_path(repository_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repository_root.resolve()).as_posix()


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _latency_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    values = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    return {
        "method": LATENCY_METHOD,
        "request_count": len(rows),
        "measured_response_count": len(values),
        "missing_count": len(rows) - len(values),
        "sum_ms": _rounded(sum(values)) if values else None,
        "mean_ms": _rounded(statistics.fmean(values)) if values else None,
        "median_ms": _rounded(statistics.median(values)) if values else None,
        "p95_ms": _rounded(_percentile(values, 0.95)),
        "max_ms": _rounded(max(values)) if values else None,
    }


def _known_cost(records: Iterable[dict[str, Any]]) -> str:
    value = sum(
        (Decimal(str(row["cost_usd"])) for row in records if row.get("cost_usd") is not None),
        Decimal(0),
    )
    return format(value.quantize(Decimal("0.000000001")), "f")


def _journal_projection(journal_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{journal_path.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        idempotency_to_trial: dict[str, str] = {}
        diagnostics: dict[str, Counter[str]] = defaultdict(Counter)
        parse_failures: list[dict[str, Any]] = []
        response_by_identity: dict[tuple[str, int, int], str] = {}
        event_counts: Counter[str] = Counter()
        for trial_id, step_index, attempt_index, kind, payload_bytes in connection.execute(
            "SELECT trial_id, step_index, attempt_index, kind, payload "
            "FROM events ORDER BY sequence"
        ):
            payload = json.loads(bytes(payload_bytes))
            event_counts[str(kind)] += 1
            if kind == "attempt_started":
                key = str(payload["idempotency_key"])
                _require(key not in idempotency_to_trial, "journal repeats an idempotency key")
                idempotency_to_trial[key] = str(trial_id)
            elif kind == "dispatch_committed":
                diagnostics[str(trial_id)][str(payload["diagnostic"]["event"])] += 1
            elif kind == "canonical_response_persisted":
                response_by_identity[(str(trial_id), int(step_index), int(attempt_index))] = str(
                    payload["canonical_response_digest"]
                )
            elif kind == "sealed_unsuccessful_result":
                parse_failures.append(
                    {
                        "trial_id": str(trial_id),
                        "step_index": int(step_index),
                        "attempt_index": int(attempt_index),
                        "failure_code": str(payload["failure_code"]),
                        "sanitized_reason": str(payload["sanitized_reason"]),
                        "parser_version": str(payload["parser_version"]),
                    }
                )

        for failure in parse_failures:
            identity = (
                failure["trial_id"],
                failure["step_index"],
                failure["attempt_index"],
            )
            digest = response_by_identity.get(identity)
            _require(digest is not None, "parse failure has no canonical response")
            row = connection.execute(
                "SELECT data FROM objects WHERE digest = ?", (digest,)
            ).fetchone()
            _require(row is not None, "parse-failure response object is missing")
            response = json.loads(bytes(row[0]))
            content = str(response["content"])
            meaningful = content.rstrip("\t\n\r ")
            try:
                json.loads(content)
            except json.JSONDecodeError:
                pass
            else:
                raise PublicationError("recorded parse failure contains valid JSON")
            failure["response_structure"] = {
                "content_characters": len(content),
                "meaningful_characters": len(meaningful),
                "trailing_json_whitespace_characters": len(content) - len(meaningful),
                "opening_brace_count": content.count("{"),
                "closing_brace_count": content.count("}"),
                "begins_with_opening_brace": meaningful.startswith("{"),
                "finish_reason": response["finish_reason"],
                "completion_tokens": int(response["usage"]["completion_tokens"]),
            }
        return {
            "idempotency_to_trial": idempotency_to_trial,
            "diagnostics": diagnostics,
            "parse_failures": parse_failures,
            "event_counts": dict(sorted(event_counts.items())),
        }
    finally:
        connection.close()


def _group_transport_records(
    records: list[dict[str, Any]], idempotency_to_trial: dict[str, str]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed: set[str] = set()
    for record in records:
        key = str(record["idempotency_key"])
        _require(key in idempotency_to_trial, "transport record has no journal attempt")
        _require(key not in observed, "transport record repeats an idempotency key")
        observed.add(key)
        grouped[idempotency_to_trial[key]].append(record)
    _require(observed == set(idempotency_to_trial), "journal and transport attempts differ")
    return grouped


def build_audit(
    repository_root: Path,
    *,
    plan_path: Path,
    run_directory: Path,
) -> dict[str, Any]:
    """Verify the frozen run and return a response-content-free audit."""

    plan = _load_json(plan_path)
    summary_path = run_directory / "summary.json"
    journal_path = run_directory / "attempts.sqlite"
    summary = _load_json(summary_path)
    checks: list[dict[str, Any]] = []

    def verified(name: str, details: dict[str, Any]) -> None:
        checks.append({"name": name, "verified": True, **details})

    _require(plan.get("schema_version") == PLAN_SCHEMA_VERSION, "plan schema mismatch")
    _require(summary.get("schema_version") == RESULT_SCHEMA_VERSION, "result schema mismatch")
    _require(content_digest(plan) == APPROVED_PLAN_SHA256, "approved plan digest mismatch")
    _require(
        summary.get("approved_plan_sha256") == APPROVED_PLAN_SHA256,
        "summary is not bound to the approved plan",
    )
    _require(summary.get("code_revision") == plan.get("code_revision"), "code revision mismatch")
    _require(summary.get("purpose") == plan.get("purpose"), "run purpose mismatch")
    verified(
        "approved_plan_and_run_identity",
        {
            "approved_plan_sha256": APPROVED_PLAN_SHA256,
            "code_revision": summary["code_revision"],
        },
    )

    task_order = plan.get("task_order")
    results = summary.get("episode_results")
    _require(isinstance(task_order, list), "plan task order is not a list")
    _require(isinstance(results, list), "episode results are not a list")
    assigned = int(plan["assigned_policy_task_pairs"])
    _require(len(task_order) == assigned == 50, "assigned count differs from frozen plan")
    _require(
        [row["task_id"] for row in results]
        == [row["task_id"] for row in task_order[: len(results)]],
        "episode results are not the frozen task-order prefix",
    )
    for result, task in zip(results, task_order[: len(results)], strict=True):
        _require(
            result["trial_id"] == f"d56-qwen-v2-{int(task['ordinal']):02d}-{task['task_id']}",
            "trial ID is not bound to its frozen assignment",
        )
        _require(
            0 <= int(result["environment_actions"]) <= int(task["max_episode_steps"]),
            "episode action count exceeds its horizon",
        )
        if result["classification"] == "step_limit_truncation":
            _require(
                int(result["environment_actions"]) == int(task["max_episode_steps"]),
                "step-limit truncation did not exhaust its approved action horizon",
            )
    classifications = Counter(str(row["classification"]) for row in results)
    _require(summary["attempted_policy_task_pairs"] == len(results), "attempted count differs")
    _require(summary["successful_policy_task_pairs"] == 0, "success count differs")
    _require(
        summary["classifications"] == dict(sorted(classifications.items())),
        "classifications differ",
    )
    _require(not summary["completed_all_assigned_pairs"], "incomplete run marked complete")
    verified(
        "assignment_and_episode_reconciliation",
        {
            "assigned_policy_task_pairs": assigned,
            "attempted_policy_task_pairs": len(results),
            "successful_policy_task_pairs": 0,
            "classifications": dict(sorted(classifications.items())),
            "step_limit_horizons_exhausted": classifications.get(
                "step_limit_truncation", 0
            ),
        },
    )

    manifest = _historical_calibration_manifest(repository_root)
    manifest_path = repository_root / plan["calibration_partition"]["path"]
    manifest_file = _file_record(repository_root, manifest_path)
    _require(
        manifest_file["sha256"] == plan["calibration_partition"]["file_sha256"],
        "calibration manifest file changed",
    )
    _require(
        manifest["manifest_digest"] == plan["calibration_partition"]["manifest_digest"],
        "calibration manifest identity changed",
    )
    _require(
        [row["task_id"] for row in manifest["records"]] == [row["task_id"] for row in task_order],
        "manifest order differs from the approved plan",
    )
    verified(
        "calibration_partition_binding",
        {"manifest_digest": manifest["manifest_digest"], "episode_count": 50},
    )

    try:
        journal = _audit_journal(journal_path, summary["journal_integrity"])
    except IntegrityAuditError as exc:
        raise PublicationError(str(exc)) from exc
    projection = _journal_projection(journal_path)
    trial_ids = {str(row["trial_id"]) for row in results}
    _require(journal["initial_trial_ids"] == trial_ids, "journal trial set differs")
    _require(
        journal["model_attempt_reservations"] == summary["model_attempt_reservations"] == 350,
        "model-attempt counts differ",
    )
    _require(journal["provider_control_request_reservations"] == 0, "control calls differ")
    _require(
        sum(int(row["environment_actions"]) for row in results)
        == projection["event_counts"].get("dispatch_committed", 0)
        == 349,
        "committed action counts differ",
    )
    verified(
        "journal_bytes_event_chain_and_lineage",
        {
            "journal_integrity": journal["integrity"],
            "object_bytes_verified": journal["object_bytes_verified"],
            "structured_objects_verified": journal["structured_objects_verified"],
            "event_counts": projection["event_counts"],
        },
    )

    records = summary.get("transport_records")
    _require(isinstance(records, list), "transport records are not a list")
    _require(len(records) == summary["provider_wire_requests"] == 350, "wire counts differ")
    _require(
        {str(row["idempotency_key"]) for row in records} == journal["idempotency_keys"],
        "transport and journal calls differ",
    )
    _require(
        all(
            row.get("status") == "response"
            and row.get("response_model") == "qwen/qwen3-vl-8b-instruct"
            and row.get("upstream_provider") == "Alibaba"
            for row in records
        ),
        "provider response identity or status differs",
    )
    incremental = Decimal(_known_cost(records))
    known_prior = Decimal(summary["known_prior_aggregate_spend_usd"])
    budget_prior = Decimal(summary["prior_aggregate_spend_usd"])
    known_actual = Decimal(summary["actual_aggregate_spend_usd"])
    budget_actual = Decimal(summary["budget_accounted_aggregate_spend_usd"])
    maximum = Decimal(summary["maximum_aggregate_spend_usd"])
    remaining = Decimal(summary["remaining_aggregate_spend_usd"])
    _require(
        incremental == Decimal(summary["calibration_incremental_spend_usd"]), "cost sum differs"
    )
    _require(known_prior + incremental == known_actual, "known spend arithmetic differs")
    _require(budget_prior + incremental == budget_actual, "budget spend arithmetic differs")
    _require(budget_actual + remaining == maximum, "remaining cap arithmetic differs")
    verified(
        "provider_identity_calls_latency_and_spend",
        {
            "provider_wire_requests": 350,
            "completed_provider_responses": 350,
            "unknown_provider_outcomes": 0,
            "retryable_rate_limits": projection["event_counts"].get("retryable_rate_limit", 0),
            "latency": _latency_summary(records),
            "calibration_incremental_spend_usd": str(incremental),
            "known_actual_aggregate_spend_usd": str(known_actual),
            "budget_accounted_aggregate_spend_usd": str(budget_actual),
            "remaining_aggregate_spend_usd": str(remaining),
        },
    )

    failures = projection["parse_failures"]
    _require(len(failures) == 1, "expected exactly one parse failure")
    failure = failures[0]
    terminal_result = results[-1]
    _require(terminal_result["classification"] == "invalid_output", "terminal route differs")
    _require(failure["trial_id"] == terminal_result["trial_id"], "parse failure trial differs")
    _require(
        failure["step_index"] == 9 and failure["attempt_index"] == 0,
        "parse failure identity differs",
    )
    _require(
        int(terminal_result["environment_actions"]) == int(failure["step_index"])
        and int(terminal_result["model_attempts"]) == int(failure["step_index"]) + 1
        and int(terminal_result["provider_wire_requests"])
        == int(terminal_result["model_attempts"]),
        "terminal action and model-attempt counts do not bind to the parse failure",
    )
    _require(
        failure["failure_code"] == "parse_failure"
        and failure["sanitized_reason"] == "JSONDecodeError",
        "parse failure reason differs",
    )
    structure = failure["response_structure"]
    _require(
        structure["begins_with_opening_brace"]
        and structure["opening_brace_count"] == 1
        and structure["closing_brace_count"] == 0
        and structure["finish_reason"] == "stop",
        "terminal malformed-output structure differs",
    )
    verified(
        "terminal_invalid_output_route",
        {
            "task_id": terminal_result["task_id"],
            "trial_id": terminal_result["trial_id"],
            "step_index": failure["step_index"],
            "attempt_index": failure["attempt_index"],
            "environment_actions": terminal_result["environment_actions"],
            "model_attempts": terminal_result["model_attempts"],
            "provider_wire_requests": terminal_result["provider_wire_requests"],
            "failure_code": failure["failure_code"],
            "sanitized_reason": failure["sanitized_reason"],
            "parser_version": failure["parser_version"],
            "response_structure": structure,
            "retry_permitted_by_approved_plan": False,
        },
    )

    predecessor_files: list[dict[str, Any]] = []
    for evidence_key in (
        "successful_smoke_evidence",
        "frozen_qwen_429_predecessor",
        "latest_audited_spend_evidence",
    ):
        evidence = plan[evidence_key]
        for path_key, digest_key in (
            ("summary_path", "summary_sha256"),
            ("journal_path", "journal_sha256"),
        ):
            if path_key not in evidence:
                continue
            path = repository_root / evidence[path_key]
            record = _file_record(repository_root, path)
            _require(record["sha256"] == evidence[digest_key], f"{evidence_key} changed")
            predecessor_files.append(record)
    verified(
        "predecessor_and_spend_evidence_bindings",
        {
            "verified_file_count": len(predecessor_files),
            "latest_known_spend_usd": plan["latest_audited_spend_evidence"][
                "actual_aggregate_spend_usd"
            ],
            "prior_unknown_charge_reservation_usd": plan["caps"][
                "unknown_prior_charge_reservation_usd"
            ],
        },
    )

    _require(
        summary.get("cleanup") == {"journal_closed": True, "policy_and_environments_closed": True},
        "cleanup record differs",
    )
    _require(
        summary.get("publication_status") == "restricted_raw_responses_in_local_journal",
        "publication restriction differs",
    )
    _require(summary.get("execution_error") is None, "runner raised an execution exception")
    verified(
        "cleanup_and_publication_restriction",
        {
            "cleanup": summary["cleanup"],
            "publication_status": summary["publication_status"],
        },
    )

    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "purpose": "no-call integrity audit of the frozen Qwen3-VL calibration evidence",
        "provider_calls_made": 0,
        "publication_status": "publishable_no_provider_payloads",
        "audit_implementation": _file_record(repository_root, Path(__file__)),
        "artifacts": {
            "approved_plan": _file_record(repository_root, plan_path),
            "run_summary": _file_record(repository_root, summary_path),
            "attempt_journal": _file_record(repository_root, journal_path),
            "calibration_manifest": manifest_file,
        },
        "checks": checks,
        "result": {
            "checks_verified": len(checks),
            "checks_failed": 0,
            "provider_calls_made": 0,
            "milestone_gate_verdict": "not_evaluated_human_owned",
        },
    }
    validate_credential_free(report)
    return report


def build_derivative(
    *,
    plan: dict[str, Any],
    summary: dict[str, Any],
    manifest: dict[str, Any],
    audit: dict[str, Any],
    projection: dict[str, Any],
    audit_file_sha256: str,
) -> dict[str, Any]:
    """Build a response-content-free derivative from verified stored evidence."""

    _require(audit["result"]["checks_failed"] == 0, "integrity audit has failed checks")
    result_by_task = {str(row["task_id"]): row for row in summary["episode_results"]}
    manifest_by_task = {str(row["task_id"]): row for row in manifest["records"]}
    transport_by_trial = _group_transport_records(
        summary["transport_records"], projection["idempotency_to_trial"]
    )
    diagnostics_by_trial: dict[str, Counter[str]] = projection["diagnostics"]
    tasks: list[dict[str, Any]] = []
    for approved in plan["task_order"]:
        task_id = str(approved["task_id"])
        seed = manifest_by_task[task_id]["seed_record"]
        result = result_by_task.get(task_id)
        trial_id = None if result is None else str(result["trial_id"])
        transport = [] if trial_id is None else transport_by_trial.get(trial_id, [])
        diagnostics = Counter() if trial_id is None else diagnostics_by_trial[trial_id]
        tasks.append(
            {
                "ordinal": int(approved["ordinal"]),
                "task_id": task_id,
                "family": str(seed["family"]),
                "difficulty_band": str(seed["difficulty_band"]),
                "logical_id": str(seed["logical_id"]),
                "variant": str(seed["variant"]),
                "max_episode_steps": int(approved["max_episode_steps"]),
                "attempted": result is not None,
                "outcome": "unattempted_due_to_prior_invalid_output_stop"
                if result is None
                else str(result["classification"]),
                "success": None if result is None else bool(result["success"]),
                "environment_actions": 0 if result is None else int(result["environment_actions"]),
                "model_attempts": 0 if result is None else int(result["model_attempts"]),
                "provider_wire_requests": 0
                if result is None
                else int(result["provider_wire_requests"]),
                "completed_provider_responses": sum(
                    row["status"] == "response" for row in transport
                ),
                "known_cost_usd": _known_cost(transport),
                "latency": _latency_summary(transport),
                "diagnostic_event_counts": dict(sorted(diagnostics.items())),
            }
        )

    family_order = list(dict.fromkeys(str(row["family"]) for row in plan["task_order"]))
    families: list[dict[str, Any]] = []
    for family in family_order:
        rows = [row for row in tasks if row["family"] == family]
        attempted = [row for row in rows if row["attempted"]]
        transport = [
            record
            for task in attempted
            for record in transport_by_trial[result_by_task[task["task_id"]]["trial_id"]]
        ]
        outcomes = Counter(str(row["outcome"]) for row in rows)
        diagnostics = Counter()
        for task in attempted:
            diagnostics.update(task["diagnostic_event_counts"])
        attempted_cap = sum(int(row["max_episode_steps"]) for row in attempted)
        actions = sum(int(row["environment_actions"]) for row in attempted)
        families.append(
            {
                "family": family,
                "assigned_tasks": len(rows),
                "attempted_tasks": len(attempted),
                "successful_tasks": outcomes["success_termination"],
                "step_limit_truncations": outcomes["step_limit_truncation"],
                "invalid_outputs": outcomes["invalid_output"],
                "unattempted_tasks": outcomes["unattempted_due_to_prior_invalid_output_stop"],
                "assigned_action_cap": sum(int(row["max_episode_steps"]) for row in rows),
                "attempted_action_cap": attempted_cap,
                "environment_actions": actions,
                "attempted_action_cap_utilization": None
                if not attempted_cap
                else round(actions / attempted_cap, 9),
                "provider_wire_requests": len(transport),
                "completed_provider_responses": len(transport),
                "known_cost_usd": _known_cost(transport),
                "latency": _latency_summary(transport),
                "diagnostic_event_counts": dict(sorted(diagnostics.items())),
            }
        )

    pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if str(task["variant"]).startswith("twin_"):
            pair_groups[str(task["logical_id"])].append(task)
    pairs: list[dict[str, Any]] = []
    unpaired: list[dict[str, Any]] = []
    for logical_id, rows in sorted(pair_groups.items()):
        if len(rows) != 2 or {row["variant"] for row in rows} != {"twin_a", "twin_b"}:
            unpaired.extend(
                {
                    "logical_id": logical_id,
                    "task_id": row["task_id"],
                    "family": row["family"],
                    "variant": row["variant"],
                }
                for row in rows
            )
            continue
        ordered = sorted(rows, key=lambda row: row["variant"])
        attempted = all(row["attempted"] for row in ordered)
        consistency = (
            "unattempted"
            if not attempted
            else "concordant"
            if ordered[0]["success"] == ordered[1]["success"]
            else "discordant"
        )
        pairs.append(
            {
                "logical_id": logical_id,
                "family": ordered[0]["family"],
                "attempted": attempted,
                "consistency": consistency,
                "twin_a": {
                    "task_id": ordered[0]["task_id"],
                    "outcome": ordered[0]["outcome"],
                    "environment_actions": ordered[0]["environment_actions"],
                },
                "twin_b": {
                    "task_id": ordered[1]["task_id"],
                    "outcome": ordered[1]["outcome"],
                    "environment_actions": ordered[1]["environment_actions"],
                },
            }
        )

    attempted_tasks = [row for row in tasks if row["attempted"]]
    attempted_pairs = [row for row in pairs if row["attempted"]]
    attempted_cap = sum(int(row["max_episode_steps"]) for row in attempted_tasks)
    actions = sum(int(row["environment_actions"]) for row in attempted_tasks)
    diagnostics = Counter()
    for task in attempted_tasks:
        diagnostics.update(task["diagnostic_event_counts"])
    failure = projection["parse_failures"][0]
    terminal_result = summary["episode_results"][-1]
    invalid_route = {
        "task_id": terminal_result["task_id"],
        "trial_id": failure["trial_id"],
        "step_index": failure["step_index"],
        "attempt_index": failure["attempt_index"],
        "failure_code": failure["failure_code"],
        "sanitized_reason": failure["sanitized_reason"],
        "parser_version": failure["parser_version"],
        "response_structure": failure["response_structure"],
        "retry_permitted_by_approved_plan": False,
    }
    policy_manifest = plan["policy"]["policy_manifest"]
    phase_spend = legacy_summary_spend_disclosure(summary)
    campaign_spend = legacy_campaign_spend_disclosure(summary)
    derivative = {
        "schema_version": DERIVATIVE_SCHEMA_VERSION,
        "purpose": "publishable derivative of the incomplete Qwen3-VL D5.6 calibration run",
        "status": "incomplete_negative_calibration_evidence_not_a_benchmark_score",
        "source_bindings": {
            "approved_plan_sha256": summary["approved_plan_sha256"],
            "run_summary_sha256": audit["artifacts"]["run_summary"]["sha256"],
            "restricted_attempt_journal_sha256": audit["artifacts"]["attempt_journal"]["sha256"],
            "journal_integrity": summary["journal_integrity"],
            "calibration_manifest_digest": manifest["manifest_digest"],
            "integrity_audit_sha256": audit_file_sha256,
            "code_revision": summary["code_revision"],
        },
        "policy": {
            "slot": plan["policy"]["slot"],
            "policy_id": policy_manifest["policy_id"],
            "policy_manifest_digest": plan["policy"]["policy_manifest_digest"],
            "model": policy_manifest["model"],
            "provider": policy_manifest["provider"],
            "coordinate_adapter": policy_manifest["coordinate_adapter"],
            "memory_policy_version": policy_manifest["memory_policy_version"],
            "parser_version": policy_manifest["parser_version"],
            "response_schema_version": policy_manifest["response_schema_version"],
            "max_model_attempts_per_action": policy_manifest["max_model_attempts_per_action"],
            "transport_retry_rule": policy_manifest["transport_retry_rule"],
        },
        "coverage": {
            "assigned_tasks": len(tasks),
            "attempted_tasks": len(attempted_tasks),
            "successful_tasks": 0,
            "step_limit_truncations": sum(
                row["outcome"] == "step_limit_truncation" for row in tasks
            ),
            "invalid_outputs": sum(row["outcome"] == "invalid_output" for row in tasks),
            "unattempted_tasks": sum(not row["attempted"] for row in tasks),
            "completed_all_assigned_tasks": False,
        },
        "action_budget": {
            "assigned_environment_action_cap": sum(int(row["max_episode_steps"]) for row in tasks),
            "attempted_environment_action_cap": attempted_cap,
            "committed_environment_actions": actions,
            "attempted_action_cap_utilization": round(actions / attempted_cap, 9),
        },
        "provider_requests": {
            "wire_requests": len(summary["transport_records"]),
            "completed_responses": len(summary["transport_records"]),
            "unknown_outcomes": 0,
            "retryable_rate_limits": projection["event_counts"].get("retryable_rate_limit", 0),
            "control_requests": int(summary["provider_control_requests"]),
            "latency": _latency_summary(summary["transport_records"]),
        },
        "cost": {
            "phase_spend": phase_spend,
            "campaign_spend": campaign_spend,
            "known_calibration_incremental_spend_usd": summary["calibration_incremental_spend_usd"],
            "known_prior_aggregate_spend_usd": summary["known_prior_aggregate_spend_usd"],
            "prior_unknown_charge_reservation_usd": summary["unknown_prior_charge_reservation_usd"],
            "budget_accounted_prior_spend_usd": summary["prior_aggregate_spend_usd"],
            "known_actual_aggregate_spend_usd": summary["actual_aggregate_spend_usd"],
            "budget_accounted_aggregate_spend_usd": summary["budget_accounted_aggregate_spend_usd"],
            "recorded_remaining_aggregate_spend_usd": summary["remaining_aggregate_spend_usd"],
            "maximum_aggregate_spend_usd": summary["maximum_aggregate_spend_usd"],
            "current_run_unconfirmed_charge_outcomes": 0,
        },
        "failure_routes": {
            "terminal_classification_counts": dict(
                sorted(Counter(str(row["outcome"]) for row in tasks).items())
            ),
            "committed_action_diagnostic_counts": dict(sorted(diagnostics.items())),
            "invalid_output": invalid_route,
        },
        "robustness": {
            "complete_pair_count": len(pairs),
            "attempted_pair_count": len(attempted_pairs),
            "concordant_attempted_pair_count": sum(
                row["consistency"] == "concordant" for row in attempted_pairs
            ),
            "discordant_attempted_pair_count": sum(
                row["consistency"] == "discordant" for row in attempted_pairs
            ),
            "unattempted_pair_count": sum(not row["attempted"] for row in pairs),
            "pairs": pairs,
            "unpaired_twin_records": unpaired,
        },
        "families": families,
        "tasks": tasks,
        "redaction": {
            "policy_version": REDACTION_POLICY_VERSION,
            "excluded": [
                "raw provider responses and response identifiers",
                "provider request bodies, prompts, and task instructions",
                "screenshots and screenshot bytes",
                "policy-state and environment-checkpoint contents",
                "hostnames, usernames, account identifiers, and absolute local paths",
                "idempotency keys and private transport metadata",
            ],
            "retained": [
                "content and file digests",
                "task and trial identifiers",
                "terminal classifications and action counts",
                "aggregate provider identity, request counts, latency, and cost",
                "privileged evaluator diagnostic event names and counts",
                "non-content structural facts about the terminal malformed response",
            ],
        },
        "limitations": [
            "The approved run stopped after the first invalid output; 37 assignments were not attempted.",
            "The 13 attempted tasks produced no exact-success termination; 12 exhausted their full horizons.",
            "The terminal response was structurally incomplete despite a provider finish reason of stop; raw text remains restricted.",
            "No HTTP 429 occurred, so the bounded rate-limit retry path was not exercised by this run.",
            "Known aggregate spend excludes the prior Gemini request with unconfirmed charge status; budget-accounted spend retains its full reservation.",
            "Calibration evidence is separate from confirmatory evaluation and is not a final benchmark score.",
            "This single-policy successor does not satisfy the planned four-policy calibration comparison by itself.",
        ],
    }
    validate_credential_free(derivative)
    return derivative


def _pct(numerator: int, denominator: int) -> str:
    return "n/a" if denominator == 0 else f"{100 * numerator / denominator:.1f}%"


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.1f}"


def _format_cost(value: str) -> str:
    if value == "unknown":
        return "unknown"
    return f"${Decimal(value):.9f}"


def _diagnostic_text(counts: dict[str, int]) -> str:
    return "—" if not counts else "; ".join(f"`{name}`={count}" for name, count in counts.items())


def render_report(derivative: dict[str, Any], *, derivative_sha256: str) -> str:
    """Render the human-readable report from the publishable derivative only."""

    coverage = derivative["coverage"]
    action = derivative["action_budget"]
    requests = derivative["provider_requests"]
    cost = derivative["cost"]
    invalid = derivative["failure_routes"]["invalid_output"]
    structure = invalid["response_structure"]
    spend_disclosure_rows: list[str] = []
    if derivative.get("schema_version") == DERIVATIVE_SCHEMA_VERSION:
        phase_spend = cost["phase_spend"]
        campaign_spend = cost["campaign_spend"]
        spend_disclosure_rows = [
            f"| Phase known spend | {_format_cost(phase_spend['known_spend_usd'])} |",
            (
                "| Phase unknown reservation | "
                f"{_format_cost(phase_spend['unknown_reservation_usd'])} |"
            ),
            (
                "| Phase in-flight reservation | "
                f"{_format_cost(phase_spend['in_flight_reservation_usd'])} |"
            ),
            (
                "| Phase budget-accounted spend | "
                f"{_format_cost(phase_spend['budget_accounted_spend_usd'])} |"
            ),
            (
                "| Campaign known spend | "
                f"{_format_cost(campaign_spend['known_spend_usd'])} |"
            ),
            (
                "| Campaign unknown reservation | "
                f"{_format_cost(campaign_spend['unknown_reservation_usd'])} |"
            ),
            (
                "| Campaign budget-accounted spend | "
                f"{_format_cost(campaign_spend['budget_accounted_spend_usd'])} |"
            ),
        ]
    lines = [
        "# Qwen3-VL 8B v5 Calibration Report",
        "",
        "**Status:** incomplete negative calibration evidence; not a benchmark score or milestone-gate verdict",
        "",
        (
            "This report describes the frozen `B-qwen-stateful-v2` run from its sealed, "
            "response-content-free derivative. The run retained all 50 assignments in the "
            "denominator and stopped at the first invalid output, as its approved plan required."
        ),
        "",
        "## Result and coverage",
        "",
        "| Measure | Stored result |",
        "|---|---:|",
        f"| Assigned tasks | {coverage['assigned_tasks']} |",
        f"| Attempted tasks | {coverage['attempted_tasks']} |",
        (
            f"| Exact-success terminations | {coverage['successful_tasks']} / "
            f"{coverage['attempted_tasks']} attempted "
            f"({_pct(coverage['successful_tasks'], coverage['attempted_tasks'])}) |"
        ),
        f"| Step-limit truncations | {coverage['step_limit_truncations']} |",
        f"| Invalid outputs | {coverage['invalid_outputs']} |",
        f"| Unattempted assignments | {coverage['unattempted_tasks']} |",
        "| Completed all assigned tasks | no |",
        "",
        (
            "The 0/13 attempted-task success rate is descriptive negative calibration evidence. "
            "It is not a complete-run or confirmatory score."
        ),
        "",
        "## Family breakdown",
        "",
        "| Family | Assigned | Attempted | Success | Truncated | Invalid | Unattempted | Actions / attempted cap | Known cost | Median / P95 latency (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family in derivative["families"]:
        lines.append(
            f"| `{family['family']}` | {family['assigned_tasks']} | "
            f"{family['attempted_tasks']} | {family['successful_tasks']} | "
            f"{family['step_limit_truncations']} | {family['invalid_outputs']} | "
            f"{family['unattempted_tasks']} | {family['environment_actions']} / "
            f"{family['attempted_action_cap']} | {_format_cost(family['known_cost_usd'])} | "
            f"{_format_ms(family['latency']['median_ms'])} / "
            f"{_format_ms(family['latency']['p95_ms'])} |"
        )
    lines.extend(
        [
            "",
            "## Action budget",
            "",
            "| Measure | Stored result |",
            "|---|---:|",
            f"| Assigned environment-action cap | {action['assigned_environment_action_cap']} |",
            f"| Attempted-task action cap | {action['attempted_environment_action_cap']} |",
            f"| Committed environment actions | {action['committed_environment_actions']} |",
            f"| Attempted-cap utilization | {100 * action['attempted_action_cap_utilization']:.1f}% |",
            "",
            "The 12 truncations consumed their full task horizons. The terminal invalid-output task committed nine actions before parsing failed at step 9.",
            "",
            "## Provider latency and cost",
            "",
            (
                "Latency covers all completed response records and uses linear interpolation "
                "over sorted response latencies for P95."
            ),
            "",
            "| Measure | Stored result |",
            "|---|---:|",
            f"| Provider wire requests | {requests['wire_requests']} |",
            f"| Completed responses | {requests['completed_responses']} |",
            f"| Unknown outcomes | {requests['unknown_outcomes']} |",
            f"| HTTP 429 retry events | {requests['retryable_rate_limits']} |",
            f"| Mean response latency | {_format_ms(requests['latency']['mean_ms'])} ms |",
            f"| Median response latency | {_format_ms(requests['latency']['median_ms'])} ms |",
            f"| P95 response latency | {_format_ms(requests['latency']['p95_ms'])} ms |",
            f"| Maximum response latency | {_format_ms(requests['latency']['max_ms'])} ms |",
            f"| Qwen incremental spend | {_format_cost(cost['known_calibration_incremental_spend_usd'])} |",
            f"| Known aggregate spend | {_format_cost(cost['known_actual_aggregate_spend_usd'])} |",
            f"| Reserved prior Gemini exposure | {_format_cost(cost['prior_unknown_charge_reservation_usd'])} |",
            f"| Budget-accounted aggregate spend | {_format_cost(cost['budget_accounted_aggregate_spend_usd'])} |",
            f"| Remaining shared cap | {_format_cost(cost['recorded_remaining_aggregate_spend_usd'])} |",
            *spend_disclosure_rows,
            "",
            "## Terminal invalid output",
            "",
            (
                f"Task `{invalid['task_id']}` stopped at step {invalid['step_index']} with "
                f"`{invalid['sanitized_reason']}`. The response contained "
                f"{structure['content_characters']} characters, of which "
                f"{structure['meaningful_characters']} were non-trailing JSON whitespace. It "
                f"began with an opening brace, contained {structure['opening_brace_count']} "
                f"opening brace and {structure['closing_brace_count']} closing braces, while "
                f"the provider reported `finish_reason={structure['finish_reason']}` and "
                f"{structure['completion_tokens']} completion tokens."
            ),
            "",
            (
                "The approved retry rule covered confirmed HTTP 429 and exact zero-token, "
                "zero-cost error envelopes only. It did not permit retrying malformed JSON, so "
                "the runner retained the invalid response and stopped without replacing the task."
            ),
            "",
            "## Robustness-pair breakdown",
            "",
            (
                f"The partition contains {derivative['robustness']['complete_pair_count']} complete "
                f"logical twin pairs. {derivative['robustness']['attempted_pair_count']} were "
                f"attempted; {derivative['robustness']['unattempted_pair_count']} were not. All "
                "attempted pairs were concordant failures because neither twin succeeded."
            ),
            "",
            "| Logical pair | Family | Twin A | Twin B | Consistency |",
            "|---|---|---|---|---|",
        ]
    )
    for pair in derivative["robustness"]["pairs"]:
        lines.append(
            f"| `{pair['logical_id']}` | `{pair['family']}` | "
            f"`{pair['twin_a']['outcome']}` ({pair['twin_a']['environment_actions']} actions) | "
            f"`{pair['twin_b']['outcome']}` ({pair['twin_b']['environment_actions']} actions) | "
            f"`{pair['consistency']}` |"
        )
    lines.extend(
        [
            "",
            "## Failure routes",
            "",
            "| Terminal route | Tasks |",
            "|---|---:|",
        ]
    )
    for route, count in derivative["failure_routes"]["terminal_classification_counts"].items():
        lines.append(f"| `{route}` | {count} |")
    lines.extend(
        [
            "",
            "| Privileged diagnostic event | Count |",
            "|---|---:|",
        ]
    )
    for event, count in derivative["failure_routes"]["committed_action_diagnostic_counts"].items():
        lines.append(f"| `{event}` | {count} |")
    lines.extend(
        [
            "",
            "## Task breakdown",
            "",
            "| # | Task | Family | Band | Variant | Outcome | Actions / cap | Requests | Known cost | Median / P95 latency (ms) | Diagnostics |",
            "|---:|---|---|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for task in derivative["tasks"]:
        lines.append(
            f"| {task['ordinal']} | `{task['task_id']}` | `{task['family']}` | "
            f"`{task['difficulty_band']}` | `{task['variant']}` | `{task['outcome']}` | "
            f"{task['environment_actions']} / {task['max_episode_steps']} | "
            f"{task['provider_wire_requests']} | {_format_cost(task['known_cost_usd'])} | "
            f"{_format_ms(task['latency']['median_ms'])} / "
            f"{_format_ms(task['latency']['p95_ms'])} | "
            f"{_diagnostic_text(task['diagnostic_event_counts'])} |"
        )
    lines.extend(
        [
            "",
            "## Evidence and redaction",
            "",
            f"- Report schema: `{REPORT_SCHEMA_VERSION}`",
            f"- Approved plan: `{derivative['source_bindings']['approved_plan_sha256']}`",
            f"- Restricted journal: `{derivative['source_bindings']['restricted_attempt_journal_sha256']}`",
            f"- Journal event chain: `{derivative['source_bindings']['journal_integrity']['event_chain_digest']}`",
            f"- Integrity audit: `{derivative['source_bindings']['integrity_audit_sha256']}`",
            f"- Publishable derivative: `{derivative_sha256}`",
            "- [Publishable derivative](grounding-v5-d56-qwen-full-calibration-publishable.json)",
            "- [Integrity audit](grounding-v5-d56-qwen-full-calibration-integrity-audit.json)",
            "- [Publication relation](grounding-v5-d56-qwen-full-calibration-publication-relation.json)",
            "- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)",
            "",
            (
                "The derivative excludes provider response bodies and identifiers, request "
                "bodies, prompts, task instructions, screenshots, checkpoint contents, host "
                "paths, idempotency keys, and private transport metadata. The restricted journal "
                "remains local and is not part of this publication package."
            ),
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in derivative["limitations"])
    lines.extend(
        [
            (
                "- This report was generated from frozen calibration evidence without model "
                "calls; it does not reinterpret unattempted assignments as failures or successes."
            ),
            (
                "- The human owner retains the D5.10 benchmark verdict and approval of public "
                "resume or README performance wording."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace publication artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def publish(
    repository_root: Path,
    *,
    plan_path: Path,
    run_directory: Path,
    manifest_path: Path,
    audit_path: Path,
    derivative_path: Path,
    relation_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    for output in (audit_path, derivative_path, relation_path, report_path):
        if output.exists():
            raise FileExistsError(f"refusing to replace publication artifact: {output}")
    audit = build_audit(repository_root, plan_path=plan_path, run_directory=run_directory)
    _write_new(audit_path, json.dumps(audit, indent=2, sort_keys=True) + "\n")
    audit_sha256 = _file_digest(audit_path)
    plan = _load_json(plan_path)
    summary_path = run_directory / "summary.json"
    journal_path = run_directory / "attempts.sqlite"
    summary = _load_json(summary_path)
    manifest = _load_json(manifest_path)
    _require(
        _file_digest(summary_path) == audit["artifacts"]["run_summary"]["sha256"], "summary changed"
    )
    _require(
        _file_digest(journal_path) == audit["artifacts"]["attempt_journal"]["sha256"],
        "journal changed",
    )
    _require(
        _file_digest(manifest_path) == audit["artifacts"]["calibration_manifest"]["sha256"],
        "manifest changed",
    )
    derivative = build_derivative(
        plan=plan,
        summary=summary,
        manifest=manifest,
        audit=audit,
        projection=_journal_projection(journal_path),
        audit_file_sha256=audit_sha256,
    )
    _write_new(derivative_path, json.dumps(derivative, indent=2, sort_keys=True) + "\n")
    derivative_sha256 = _file_digest(derivative_path)
    _write_new(report_path, render_report(derivative, derivative_sha256=derivative_sha256))
    relation = {
        "schema_version": RELATION_SCHEMA_VERSION,
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "authoritative": {
            "approved_plan_content_sha256": summary["approved_plan_sha256"],
            "run_summary_file_sha256": audit["artifacts"]["run_summary"]["sha256"],
            "restricted_attempt_journal_file_sha256": audit["artifacts"]["attempt_journal"][
                "sha256"
            ],
            "journal_event_chain_sha256": summary["journal_integrity"]["event_chain_digest"],
            "integrity_audit_file_sha256": audit_sha256,
        },
        "publishable": {
            "derivative_path": _relative_path(repository_root, derivative_path),
            "derivative_content_sha256": content_digest(derivative),
            "derivative_file_sha256": derivative_sha256,
            "report_path": _relative_path(repository_root, report_path),
            "report_file_sha256": _file_digest(report_path),
        },
        "excluded_authoritative_artifacts": [
            {
                "path": _relative_path(repository_root, journal_path),
                "sha256": audit["artifacts"]["attempt_journal"]["sha256"],
                "size_bytes": audit["artifacts"]["attempt_journal"]["size_bytes"],
                "reason": "restricted provider responses, screenshots, and private checkpoints",
                "git_status": "must_not_commit",
            }
        ],
    }
    validate_credential_free(relation)
    _write_new(relation_path, json.dumps(relation, indent=2, sort_keys=True) + "\n")
    return {
        "provider_calls_made": 0,
        "audit": {"path": _relative_path(repository_root, audit_path), "sha256": audit_sha256},
        "derivative": {
            "path": _relative_path(repository_root, derivative_path),
            "sha256": derivative_sha256,
        },
        "report": {
            "path": _relative_path(repository_root, report_path),
            "sha256": _file_digest(report_path),
        },
        "relation": {
            "path": _relative_path(repository_root, relation_path),
            "sha256": _file_digest(relation_path),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--derivative", type=Path, required=True)
    parser.add_argument("--relation", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    result = publish(
        root,
        plan_path=_under_root(root, args.plan),
        run_directory=_under_root(root, args.run_directory),
        manifest_path=_under_root(root, args.manifest),
        audit_path=_under_root(root, args.audit),
        derivative_path=_under_root(root, args.derivative),
        relation_path=_under_root(root, args.relation),
        report_path=_under_root(root, args.report),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
