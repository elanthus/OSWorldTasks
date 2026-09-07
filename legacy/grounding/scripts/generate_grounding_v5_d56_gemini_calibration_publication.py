#!/usr/bin/env python3
"""Generate publishable Gemini calibration evidence from sealed local artifacts.

Run with ``python -m legacy.grounding.scripts.generate_grounding_v5_d56_gemini_calibration_publication``.
"""

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

from legacy.grounding.v5.d56_spend import (
    legacy_campaign_spend_disclosure,
    legacy_summary_spend_disclosure,
)
from pixelgym.grounding.v5.contracts import REDACTION_POLICY_VERSION, content_digest

DERIVATIVE_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-calibration-publishable-v2"
RELATION_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-publication-relation-v1"
REPORT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-gemini-calibration-report-v1"
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
        for trial_id, payload_bytes in connection.execute(
            "SELECT trial_id, payload FROM events WHERE kind = 'attempt_started'"
        ):
            payload = json.loads(bytes(payload_bytes))
            idempotency_key = str(payload["idempotency_key"])
            _require(
                idempotency_key not in idempotency_to_trial,
                "journal repeats a provider idempotency key",
            )
            idempotency_to_trial[idempotency_key] = str(trial_id)

        diagnostics: dict[str, Counter[str]] = defaultdict(Counter)
        for trial_id, payload_bytes in connection.execute(
            "SELECT trial_id, payload FROM events WHERE kind = 'dispatch_committed'"
        ):
            payload = json.loads(bytes(payload_bytes))
            diagnostics[str(trial_id)][str(payload["diagnostic"]["event"])] += 1

        unknown_attempts: list[dict[str, Any]] = []
        for trial_id, step_index, payload_bytes in connection.execute(
            "SELECT trial_id, step_index, payload FROM events "
            "WHERE kind = 'unknown_outcome_infrastructure_failure' ORDER BY sequence"
        ):
            payload = json.loads(bytes(payload_bytes))
            unknown_attempts.append(
                {
                    "trial_id": str(trial_id),
                    "step_index": int(step_index),
                    "journal_failure_code": str(payload["failure_code"]),
                    "response_persisted": payload.get("response_digest") is not None,
                    "usage_available": bool(payload.get("usage")),
                }
            )
        return {
            "idempotency_to_trial": idempotency_to_trial,
            "diagnostics": diagnostics,
            "unknown_attempts": unknown_attempts,
        }
    finally:
        connection.close()


def _group_transport_records(
    records: list[dict[str, Any]], idempotency_to_trial: dict[str, str]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_keys: set[str] = set()
    for record in records:
        key = str(record["idempotency_key"])
        _require(key in idempotency_to_trial, "transport record has no journal attempt")
        _require(key not in observed_keys, "transport record repeats an idempotency key")
        observed_keys.add(key)
        grouped[idempotency_to_trial[key]].append(record)
    _require(
        observed_keys == set(idempotency_to_trial),
        "journal and transport provider attempts differ",
    )
    return grouped


def _family_order(task_order: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(record["family"]) for record in task_order))


def _outcome_label(result: dict[str, Any] | None) -> str:
    if result is None:
        return "unattempted_due_to_prior_infrastructure_stop"
    return str(result["classification"])


def build_derivative(
    *,
    plan: dict[str, Any],
    summary: dict[str, Any],
    manifest: dict[str, Any],
    audit: dict[str, Any],
    journal_projection: dict[str, Any],
    audit_file_sha256: str,
) -> dict[str, Any]:
    """Build a response-content-free derivative from already stored evidence."""

    _require(audit["result"]["checks_failed"] == 0, "integrity audit has failed checks")
    _require(audit["provider_calls_made"] == 0, "integrity audit made provider calls")
    _require(
        plan["assigned_policy_task_pairs"] == len(plan["task_order"]),
        "plan assignment count differs from task order",
    )
    result_by_task = {str(row["task_id"]): row for row in summary["episode_results"]}
    record_by_task = {str(row["task_id"]): row for row in manifest["records"]}
    _require(
        set(record_by_task) == {str(row["task_id"]) for row in plan["task_order"]},
        "manifest and approved task order differ",
    )
    transport_by_trial = _group_transport_records(
        summary["transport_records"], journal_projection["idempotency_to_trial"]
    )
    diagnostics_by_trial: dict[str, Counter[str]] = journal_projection["diagnostics"]

    tasks: list[dict[str, Any]] = []
    for approved in plan["task_order"]:
        task_id = str(approved["task_id"])
        manifest_record = record_by_task[task_id]
        seed = manifest_record["seed_record"]
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
                "outcome": _outcome_label(result),
                "success": None if result is None else bool(result["success"]),
                "environment_actions": 0 if result is None else int(result["environment_actions"]),
                "model_attempts": 0 if result is None else int(result["model_attempts"]),
                "provider_wire_requests": 0
                if result is None
                else int(result["provider_wire_requests"]),
                "completed_provider_responses": sum(
                    row["status"] == "response" for row in transport
                ),
                "unknown_provider_outcomes": sum(row["status"] == "unknown" for row in transport),
                "known_cost_usd": _known_cost(transport),
                "latency": _latency_summary(transport),
                "diagnostic_event_counts": dict(sorted(diagnostics.items())),
            }
        )

    families: list[dict[str, Any]] = []
    for family in _family_order(plan["task_order"]):
        rows = [task for task in tasks if task["family"] == family]
        attempted = [task for task in rows if task["attempted"]]
        transport = [
            record
            for task in attempted
            for record in transport_by_trial[result_by_task[task["task_id"]]["trial_id"]]
        ]
        diagnostics = Counter()
        for task in attempted:
            diagnostics.update(task["diagnostic_event_counts"])
        outcomes = Counter(task["outcome"] for task in rows)
        attempted_action_cap = sum(task["max_episode_steps"] for task in attempted)
        actions = sum(task["environment_actions"] for task in attempted)
        families.append(
            {
                "family": family,
                "assigned_tasks": len(rows),
                "attempted_tasks": len(attempted),
                "successful_tasks": outcomes["success_termination"],
                "step_limit_truncations": outcomes["step_limit_truncation"],
                "infrastructure_failures": outcomes["infrastructure_failure"],
                "unattempted_tasks": outcomes["unattempted_due_to_prior_infrastructure_stop"],
                "assigned_action_cap": sum(task["max_episode_steps"] for task in rows),
                "attempted_action_cap": attempted_action_cap,
                "environment_actions": actions,
                "attempted_action_cap_utilization": None
                if not attempted_action_cap
                else round(actions / attempted_action_cap, 9),
                "provider_wire_requests": len(transport),
                "completed_provider_responses": sum(
                    record["status"] == "response" for record in transport
                ),
                "unknown_provider_outcomes": sum(
                    record["status"] == "unknown" for record in transport
                ),
                "known_cost_usd": _known_cost(transport),
                "latency": _latency_summary(transport),
                "diagnostic_event_counts": dict(sorted(diagnostics.items())),
            }
        )

    pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if str(task["variant"]).startswith("twin_"):
            pair_groups[str(task["logical_id"])].append(task)
    robustness_pairs: list[dict[str, Any]] = []
    unpaired_twin_records: list[dict[str, Any]] = []
    for logical_id, rows in sorted(pair_groups.items()):
        if len(rows) != 2 or {row["variant"] for row in rows} != {"twin_a", "twin_b"}:
            unpaired_twin_records.extend(
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
        if not attempted:
            consistency = "unattempted"
        elif ordered[0]["success"] == ordered[1]["success"]:
            consistency = "concordant"
        else:
            consistency = "discordant"
        robustness_pairs.append(
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

    attempted_pairs = [pair for pair in robustness_pairs if pair["attempted"]]
    all_transport = summary["transport_records"]
    attempted_tasks = [task for task in tasks if task["attempted"]]
    attempted_action_cap = sum(task["max_episode_steps"] for task in attempted_tasks)
    environment_actions = sum(task["environment_actions"] for task in attempted_tasks)
    diagnostic_counts = Counter()
    for task in attempted_tasks:
        diagnostic_counts.update(task["diagnostic_event_counts"])

    unknown_attempts = journal_projection["unknown_attempts"]
    transport_unknown = [record for record in all_transport if record["status"] == "unknown"]
    _require(len(unknown_attempts) == len(transport_unknown), "unknown attempt counts differ")
    unknown_outcomes: list[dict[str, Any]] = []
    for unknown in unknown_attempts:
        result = next(
            row for row in summary["episode_results"] if row["trial_id"] == unknown["trial_id"]
        )
        transport = next(
            row
            for row in transport_unknown
            if journal_projection["idempotency_to_trial"][row["idempotency_key"]]
            == unknown["trial_id"]
        )
        unknown_outcomes.append(
            {
                "task_id": result["task_id"],
                "trial_id": unknown["trial_id"],
                "step_index": unknown["step_index"],
                "journal_failure_code": unknown["journal_failure_code"],
                "transport_failure_code": transport.get("failure_code"),
                "response_persisted": unknown["response_persisted"],
                "usage_available": unknown["usage_available"],
                "charge_status": "unconfirmed",
            }
        )

    response_cost = _known_cost(all_transport)
    _require(
        Decimal(response_cost) == Decimal(summary["calibration_incremental_spend_usd"]),
        "known response costs differ from the stored run spend",
    )
    _require(
        len(tasks) == int(summary["assigned_policy_task_pairs"]),
        "derivative task count differs from the summary denominator",
    )
    _require(
        sum(task["attempted"] for task in tasks) == int(summary["attempted_policy_task_pairs"]),
        "derivative attempted count differs from summary",
    )
    _require(
        sum(task["success"] is True for task in tasks)
        == int(summary["successful_policy_task_pairs"]),
        "derivative success count differs from summary",
    )

    policy_manifest = plan["policy"]["policy_manifest"]
    phase_spend = legacy_summary_spend_disclosure(summary)
    campaign_spend = legacy_campaign_spend_disclosure(summary)
    return {
        "schema_version": DERIVATIVE_SCHEMA_VERSION,
        "purpose": "publishable derivative of the incomplete Gemini v2 D5.6 calibration run",
        "status": "incomplete_calibration_evidence_not_a_benchmark_score",
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
            "provider_sdk_auto_retries": policy_manifest["provider_sdk_auto_retries"],
            "proxy_auto_retries": policy_manifest["proxy_auto_retries"],
        },
        "coverage": {
            "assigned_tasks": len(tasks),
            "attempted_tasks": len(attempted_tasks),
            "successful_tasks": sum(task["success"] is True for task in tasks),
            "step_limit_truncations": sum(
                task["outcome"] == "step_limit_truncation" for task in tasks
            ),
            "infrastructure_failures": sum(
                task["outcome"] == "infrastructure_failure" for task in tasks
            ),
            "unattempted_tasks": sum(not task["attempted"] for task in tasks),
            "completed_all_assigned_tasks": bool(summary["completed_all_assigned_pairs"]),
        },
        "action_budget": {
            "assigned_environment_action_cap": sum(task["max_episode_steps"] for task in tasks),
            "attempted_environment_action_cap": attempted_action_cap,
            "committed_environment_actions": environment_actions,
            "attempted_action_cap_utilization": round(
                environment_actions / attempted_action_cap, 9
            ),
        },
        "provider_requests": {
            "wire_requests": len(all_transport),
            "completed_responses": sum(record["status"] == "response" for record in all_transport),
            "unknown_outcomes": len(transport_unknown),
            "control_requests": int(summary["provider_control_requests"]),
            "latency": _latency_summary(all_transport),
        },
        "cost": {
            "phase_spend": phase_spend,
            "campaign_spend": campaign_spend,
            "known_calibration_incremental_spend_usd": response_cost,
            "prior_aggregate_spend_usd": summary["prior_aggregate_spend_usd"],
            "known_actual_aggregate_spend_usd": summary["actual_aggregate_spend_usd"],
            "recorded_remaining_aggregate_spend_usd": summary["remaining_aggregate_spend_usd"],
            "maximum_aggregate_spend_usd": summary["maximum_aggregate_spend_usd"],
            "unconfirmed_charge_outcomes": len(transport_unknown),
        },
        "failure_routes": {
            "terminal_classification_counts": dict(
                sorted(Counter(task["outcome"] for task in tasks).items())
            ),
            "committed_action_diagnostic_counts": dict(sorted(diagnostic_counts.items())),
            "unknown_provider_outcomes": unknown_outcomes,
        },
        "robustness": {
            "complete_pair_count": len(robustness_pairs),
            "attempted_pair_count": len(attempted_pairs),
            "concordant_attempted_pair_count": sum(
                pair["consistency"] == "concordant" for pair in attempted_pairs
            ),
            "discordant_attempted_pair_count": sum(
                pair["consistency"] == "discordant" for pair in attempted_pairs
            ),
            "unattempted_pair_count": sum(
                pair["consistency"] == "unattempted" for pair in robustness_pairs
            ),
            "pairs": robustness_pairs,
            "unpaired_twin_records": unpaired_twin_records,
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
                "aggregate provider identity, request counts, latency, usage availability, and cost",
                "privileged evaluator diagnostic event names and counts",
                "dispatch and unknown-outcome classifications",
            ],
        },
        "limitations": [
            "The run stopped after one unknown provider outcome; nine assigned tasks were not attempted.",
            "All evidence_aggregation tasks and one review_and_commit task were unattempted.",
            "Known spend excludes any unconfirmed charge for the unknown provider outcome.",
            "Latency summaries exclude the unknown request because it has no retained latency value.",
            "Calibration results are separate from confirmatory evaluation and are not a final benchmark score.",
            "This single-policy successor run does not satisfy the planned four-policy calibration comparison by itself.",
        ],
    }


def _pct(numerator: int, denominator: int) -> str:
    return "n/a" if denominator == 0 else f"{100 * numerator / denominator:.1f}%"


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.1f}"


def _format_cost(value: str) -> str:
    if value == "unknown":
        return "unknown"
    return f"${Decimal(value):.9f}"


def _diagnostic_text(counts: dict[str, int]) -> str:
    if not counts:
        return "—"
    return "; ".join(f"`{name}`={count}" for name, count in counts.items())


def render_report(derivative: dict[str, Any], *, derivative_sha256: str) -> str:
    """Render the human-readable calibration report from the derivative only."""

    coverage = derivative["coverage"]
    unobserved_families = [
        family["family"] for family in derivative["families"] if family["attempted_tasks"] == 0
    ]
    unobserved_text = (
        " In particular, the run contains no observations for "
        + ", ".join(f"`{family}`" for family in unobserved_families)
        + "."
        if unobserved_families
        else ""
    )
    lines = [
        "# Gemini 3.7 Flash v5 Calibration Report",
        "",
        "**Status:** incomplete calibration evidence; not a benchmark score or milestone-gate verdict",
        "",
        (
            "This report describes the frozen `A-gemini-stateful-v2` successor run using only "
            "its sealed evidence and publishable derivative. The run stopped after an unknown "
            "provider outcome, so missing assignments remain visible and no denominator is changed."
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
        f"| Infrastructure failures | {coverage['infrastructure_failures']} |",
        f"| Unattempted assignments | {coverage['unattempted_tasks']} |",
        (
            "| Completed all assigned tasks | "
            f"{'yes' if coverage['completed_all_assigned_tasks'] else 'no'} |"
        ),
        "",
        (
            "The attempted-task percentage is descriptive calibration evidence, not a "
            f"complete-run or confirmatory score.{unobserved_text}"
        ),
        "",
        "## Family breakdown",
        "",
        "| Family | Assigned | Attempted | Success | Truncated | Infrastructure | Unattempted | Actions / attempted cap | Known cost | Median latency (ms) | P95 latency (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family in derivative["families"]:
        lines.append(
            f"| `{family['family']}` | {family['assigned_tasks']} | "
            f"{family['attempted_tasks']} | {family['successful_tasks']} | "
            f"{family['step_limit_truncations']} | {family['infrastructure_failures']} | "
            f"{family['unattempted_tasks']} | {family['environment_actions']} / "
            f"{family['attempted_action_cap']} | {_format_cost(family['known_cost_usd'])} | "
            f"{_format_ms(family['latency']['median_ms'])} | "
            f"{_format_ms(family['latency']['p95_ms'])} |"
        )

    action = derivative["action_budget"]
    request = derivative["provider_requests"]
    unknown_outcomes = derivative["failure_routes"]["unknown_provider_outcomes"]
    unknown_task_by_id = {task["task_id"]: task for task in derivative["tasks"]}
    if unknown_outcomes:
        unknown_action_text = "; ".join(
            f"task `{unknown['task_id']}` committed "
            f"{unknown_task_by_id[unknown['task_id']]['environment_actions']} actions before "
            "its next provider attempt became an unknown outcome"
            for unknown in unknown_outcomes
        )
        action_note = (
            "Step-limit truncations consumed their full task horizons. Successful episodes "
            f"could terminate earlier; {unknown_action_text}."
        )
    else:
        action_note = (
            "Step-limit truncations consumed their full task horizons. Successful episodes "
            "could terminate earlier."
        )
    spend_disclosure_rows: list[str] = []
    if derivative.get("schema_version") == DERIVATIVE_SCHEMA_VERSION:
        phase_spend = derivative["cost"]["phase_spend"]
        campaign_spend = derivative["cost"]["campaign_spend"]
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
    lines.extend(
        [
            "",
            "## Action budget",
            "",
            "| Measure | Count |",
            "|---|---:|",
            f"| Assigned environment-action cap | {action['assigned_environment_action_cap']} |",
            f"| Action cap for attempted tasks | {action['attempted_environment_action_cap']} |",
            f"| Committed environment actions | {action['committed_environment_actions']} |",
            f"| Attempted-cap utilization | {100 * action['attempted_action_cap_utilization']:.1f}% |",
            "",
            action_note,
            "",
            "## Provider latency and cost",
            "",
            (
                "Latency uses completed response records only. P95 uses linear interpolation over "
                "sorted response latencies. The unknown request has no retained latency value."
            ),
            "",
            "| Measure | Stored result |",
            "|---|---:|",
            f"| Provider wire requests | {request['wire_requests']} |",
            f"| Completed provider responses | {request['completed_responses']} |",
            f"| Unknown provider outcomes | {request['unknown_outcomes']} |",
            f"| Measured latency count | {request['latency']['measured_response_count']} |",
            f"| Known latency sum | {_format_ms(request['latency']['sum_ms'])} ms |",
            f"| Mean response latency | {_format_ms(request['latency']['mean_ms'])} ms |",
            f"| Median response latency | {_format_ms(request['latency']['median_ms'])} ms |",
            f"| P95 response latency | {_format_ms(request['latency']['p95_ms'])} ms |",
            f"| Maximum response latency | {_format_ms(request['latency']['max_ms'])} ms |",
            f"| Known incremental calibration spend | {_format_cost(derivative['cost']['known_calibration_incremental_spend_usd'])} |",
            f"| Known aggregate spend | {_format_cost(derivative['cost']['known_actual_aggregate_spend_usd'])} |",
            f"| Recorded remaining shared cap | {_format_cost(derivative['cost']['recorded_remaining_aggregate_spend_usd'])} |",
            *spend_disclosure_rows,
            (
                "| Possible additional charge | "
                f"{request['unknown_outcomes']} unknown request(s); unconfirmed |"
            ),
            "",
            "## Robustness-pair breakdown",
            "",
            (
                f"The frozen {coverage['assigned_tasks']}-task successor partition contains "
                f"{derivative['robustness']['complete_pair_count']} complete logical twin pairs. "
                f"{derivative['robustness']['attempted_pair_count']} were attempted and "
                f"{derivative['robustness']['unattempted_pair_count']} were unattempted. Among "
                "attempted pairs, "
                f"{derivative['robustness']['concordant_attempted_pair_count']} were concordant "
                f"and {derivative['robustness']['discordant_attempted_pair_count']} were "
                "discordant. This is descriptive evidence of variant sensitivity; the run does "
                "not establish its cause."
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
            (
                f"{len(derivative['robustness']['unpaired_twin_records'])} additional "
                "twin-labelled record(s) have no counterpart in this successor partition and "
                "are excluded from the pair-consistency denominator."
            ),
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
            "The committed-action diagnostic stream contained:",
            "",
            "| Privileged diagnostic event | Count |",
            "|---|---:|",
        ]
    )
    for event, count in derivative["failure_routes"]["committed_action_diagnostic_counts"].items():
        lines.append(f"| `{event}` | {count} |")
    lines.append("")
    for unknown in unknown_outcomes:
        lines.append(
            f"The infrastructure route occurred on task `{unknown['task_id']}` at step "
            f"{unknown['step_index']}. The journal retained "
            f"`{unknown['journal_failure_code']}`, the transport retained "
            f"`{unknown['transport_failure_code']}`, and neither a response nor usage was "
            "persisted. Its charge status remains unconfirmed."
        )
    lines.extend(
        [
            "",
            "## Task breakdown",
            "",
            (
                "Latency and cost are sums or distributions of the provider records assigned to "
                "each task. A measured-response count smaller than requests indicates an unknown "
                "outcome."
            ),
            "",
            "| # | Task | Family | Band | Variant | Outcome | Actions / cap | Requests / responses | Known cost | Median / P95 latency (ms) | Diagnostic events |",
            "|---:|---|---|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for task in derivative["tasks"]:
        lines.append(
            f"| {task['ordinal']} | `{task['task_id']}` | `{task['family']}` | "
            f"`{task['difficulty_band']}` | `{task['variant']}` | `{task['outcome']}` | "
            f"{task['environment_actions']} / {task['max_episode_steps']} | "
            f"{task['provider_wire_requests']} / {task['completed_provider_responses']} | "
            f"{_format_cost(task['known_cost_usd'])} | "
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
            "- [Publishable derivative](grounding-v5-d56-gemini-full-calibration-publishable.json)",
            "- [Integrity audit](grounding-v5-d56-gemini-full-calibration-integrity-audit.json)",
            "- [Publication relation](grounding-v5-d56-gemini-full-calibration-publication-relation.json)",
            "- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)",
            "",
            (
                "The derivative excludes provider response bodies and identifiers, request bodies, "
                "prompts, task instructions, screenshots, checkpoint contents, host paths, "
                "idempotency keys, and private transport metadata. The restricted 1.3 GB SQLite "
                "journal remains local and is not part of this publication package."
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
                "- The report is generated from the frozen calibration evidence; it performs no "
                "model calls and does not reinterpret missing assignments as failures or successes."
            ),
            (
                "- The human owner retains the D5.10 benchmark verdict and approval of any public "
                "resume or README wording."
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--integrity-audit", type=Path, required=True)
    parser.add_argument("--derivative", type=Path, required=True)
    parser.add_argument("--relation", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv)


def _under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    plan_path = _under_root(root, args.plan)
    run_directory = _under_root(root, args.run_directory)
    manifest_path = _under_root(root, args.manifest)
    audit_path = _under_root(root, args.integrity_audit)
    derivative_path = _under_root(root, args.derivative)
    relation_path = _under_root(root, args.relation)
    report_path = _under_root(root, args.report)
    for output in (derivative_path, relation_path, report_path):
        if output.exists():
            raise FileExistsError(f"refusing to replace publication artifact: {output}")

    plan = _load_json(plan_path)
    summary_path = run_directory / "summary.json"
    journal_path = run_directory / "attempts.sqlite"
    summary = _load_json(summary_path)
    manifest = _load_json(manifest_path)
    audit = _load_json(audit_path)
    _require(
        _file_digest(summary_path) == audit["artifacts"]["run_summary"]["sha256"],
        "run summary changed after the integrity audit",
    )
    _require(
        _file_digest(journal_path) == audit["artifacts"]["attempt_journal"]["sha256"],
        "restricted journal changed after the integrity audit",
    )
    _require(
        _file_digest(manifest_path) == audit["artifacts"]["calibration_manifest"]["sha256"],
        "calibration manifest changed after the integrity audit",
    )
    audit_file_sha256 = _file_digest(audit_path)
    derivative = build_derivative(
        plan=plan,
        summary=summary,
        manifest=manifest,
        audit=audit,
        journal_projection=_journal_projection(journal_path),
        audit_file_sha256=audit_file_sha256,
    )
    derivative_text = json.dumps(derivative, indent=2, sort_keys=True) + "\n"
    _write_new(derivative_path, derivative_text)
    derivative_file_sha256 = _file_digest(derivative_path)
    report = render_report(derivative, derivative_sha256=derivative_file_sha256)
    _write_new(report_path, report)
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
            "integrity_audit_file_sha256": audit_file_sha256,
        },
        "publishable": {
            "derivative_path": _relative_path(root, derivative_path),
            "derivative_content_sha256": content_digest(derivative),
            "derivative_file_sha256": derivative_file_sha256,
            "report_path": _relative_path(root, report_path),
            "report_file_sha256": _file_digest(report_path),
        },
        "excluded_authoritative_artifacts": [
            {
                "path": _relative_path(root, journal_path),
                "sha256": audit["artifacts"]["attempt_journal"]["sha256"],
                "size_bytes": audit["artifacts"]["attempt_journal"]["size_bytes"],
                "reason": "restricted provider responses, screenshots, and private checkpoints",
                "git_status": "must_not_commit",
            }
        ],
    }
    _write_new(relation_path, json.dumps(relation, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "provider_calls_made": 0,
                "derivative": {
                    "path": _relative_path(root, derivative_path),
                    "sha256": derivative_file_sha256,
                },
                "report": {
                    "path": _relative_path(root, report_path),
                    "sha256": _file_digest(report_path),
                },
                "relation": {
                    "path": _relative_path(root, relation_path),
                    "sha256": _file_digest(relation_path),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
