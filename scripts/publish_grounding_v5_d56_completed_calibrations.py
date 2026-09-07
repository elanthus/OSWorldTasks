#!/usr/bin/env python3
"""Publish completed D5.6 calibrations from committed, response-free evidence only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import REDACTION_POLICY_VERSION, content_digest
from pixelgym.grounding.v5.evidence import validate_credential_free

DERIVATIVE_SCHEMA_VERSION = "pixelgym-agent-v5-d56-completed-calibrations-v1"
RELATION_SCHEMA_VERSION = "pixelgym-agent-v5-d56-completed-calibrations-relation-v1"
AUDIT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-calibration-publication-audit-v1"
REPORT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-completed-calibrations-report-v1"

ERRATA_PATH = Path("artifacts/grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json")
DERIVATIVE_PATH = Path(
    "artifacts/grounding-v5-d56-completed-calibrations-publishable.json"
)
REPORT_PATH = Path("artifacts/grounding-v5-d56-completed-calibrations-report.md")
RELATION_PATH = Path(
    "artifacts/grounding-v5-d56-completed-calibrations-publication-relation.json"
)
AUDIT_PATH = Path("artifacts/grounding-v5-d56-completed-calibrations-integrity-audit.json")

_LOCAL_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\Users\\\\)")
_CREDENTIAL_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~-]{12,}|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)
_RAW_PAYLOAD_KEYS = {
    "checkpoint",
    "content",
    "image",
    "prompt",
    "raw_request",
    "raw_response",
    "request",
    "request_body",
    "response",
    "response_body",
    "screenshot",
}
_CLASSIFICATION_KEYS = {
    "infrastructure_failure",
    "invalid_output",
    "policy_violation",
    "request_failure",
    "step_limit_truncation",
    "success_termination",
}


class PublicationError(RuntimeError):
    """The retained evidence cannot support the requested publication."""


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    plan_path: Path
    summary_path: Path
    relation_path: Path | None
    v3_errata_bound: bool

    @property
    def journal_path(self) -> Path:
        return self.summary_path.parent / "attempts.sqlite"


RUN_SPECS = (
    RunSpec(
        "qwen-v2",
        Path("artifacts/grounding-v5-d56-qwen-full-calibration-plan.json"),
        Path("artifacts/grounding-v5-d56-qwen-full-calibration-run/summary.json"),
        Path(
            "artifacts/grounding-v5-d56-qwen-full-calibration-publication-relation.json"
        ),
        False,
    ),
    RunSpec(
        "gemini-v3",
        Path("artifacts/grounding-v5-d56-gemini-v3-full-calibration-plan.json"),
        Path("artifacts/grounding-v5-d56-gemini-v3-full-calibration-run/summary.json"),
        None,
        True,
    ),
    RunSpec(
        "gemini-v3b",
        Path("artifacts/grounding-v5-d56-gemini-v3b-full-calibration-plan.json"),
        Path("artifacts/grounding-v5-d56-gemini-v3b-full-calibration-run/summary.json"),
        RELATION_PATH,
        True,
    ),
    RunSpec(
        "qwen-v3",
        Path("artifacts/grounding-v5-d56-qwen-v3-full-calibration-plan.json"),
        Path("artifacts/grounding-v5-d56-qwen-v3-full-calibration-run/summary.json"),
        RELATION_PATH,
        True,
    ),
)
EXPECTED_MAIN_SUMMARIES = tuple(sorted(spec.summary_path.as_posix() for spec in RUN_SPECS))
EXPECTED_PUBLISHED_RUNS = ("gemini-v3b", "qwen-v3")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PublicationError(f"expected a JSON object: {path}")
    return value


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _json_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_json_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_json_keys(child))
    return keys


def _json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_strings(child)
    elif isinstance(value, str):
        yield value


def _git(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PublicationError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _git_ref_exists(repository_root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _inventory_ref(repository_root: Path) -> str:
    for ref in ("main", "refs/remotes/origin/main", "HEAD"):
        if _git_ref_exists(repository_root, ref):
            return ref
    raise PublicationError("no Git tree is available for the full-calibration inventory")


def inventory_main(repository_root: Path) -> tuple[str, ...]:
    """Inventory main, with HEAD fallback for a detached full-history CI checkout."""

    paths = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--name-only",
        _inventory_ref(repository_root),
        "--",
        "artifacts",
    )
    summaries = tuple(
        sorted(
            line
            for line in paths.splitlines()
            if line.startswith("artifacts/grounding-v5-d56-")
            and "-full-calibration-run/" in line
            and line.endswith("/summary.json")
        )
    )
    _require(
        summaries == EXPECTED_MAIN_SUMMARIES,
        "main full-calibration inventory differs from the reviewed run set",
    )
    return summaries


def _source_commit_date(repository_root: Path, path: Path) -> str:
    output = _git(
        repository_root,
        "log",
        "--diff-filter=A",
        "--format=%cI",
        "--",
        path.as_posix(),
    )
    dates = [line for line in output.splitlines() if line]
    _require(bool(dates), f"no source-commit date for {path}")
    return dates[-1]


def _is_tracked(repository_root: Path, path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path.as_posix()],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _is_ignored(repository_root: Path, path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", path.as_posix()],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _decimal_sum(records: Iterable[dict[str, Any]]) -> Decimal:
    return sum(
        (Decimal(str(row["cost_usd"])) for row in records if row.get("cost_usd") is not None),
        Decimal(0),
    )


def _decimal_equal(left: Any, right: Any) -> bool:
    return Decimal(str(left)) == Decimal(str(right))


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000000001")), "f")


def _classification_counts(summary: dict[str, Any]) -> dict[str, int]:
    observed = Counter(str(row["classification"]) for row in summary["episode_results"])
    return {
        "attempted": len(summary["episode_results"]),
        "invalid_output": observed.get("invalid_output", 0),
        "request_failure": observed.get("request_failure", 0),
        "infrastructure_failure": observed.get("infrastructure_failure", 0),
        "policy_violation": observed.get("policy_violation", 0),
        "truncation": observed.get("step_limit_truncation", 0),
        "success": observed.get("success_termination", 0),
    }


def _errata_records(errata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["path"]): row for row in errata["source_artifacts"]}


def _redaction_is_safe(plan: dict[str, Any], summary: dict[str, Any]) -> bool:
    encoded = json.dumps([plan, summary], sort_keys=True)
    strings = tuple(_json_strings([plan, summary]))
    return (
        not _LOCAL_PATH.search(encoded)
        and not _CREDENTIAL_VALUE.search(encoded)
        and not any(
            value.startswith(("/", "file:///"))
            or re.match(r"^[A-Za-z]:[\\\\/]", value) is not None
            for value in strings
        )
        and not (_RAW_PAYLOAD_KEYS & _json_keys(plan))
        and not (_RAW_PAYLOAD_KEYS & _json_keys(summary))
    )


def _spend_record(
    repository_root: Path,
    spec: RunSpec,
    plan: dict[str, Any],
    summary: dict[str, Any],
    errata: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    records = summary["transport_records"]
    known = _decimal_sum(records)
    missing_costs = sum(row.get("cost_usd") is None for row in records)

    if "run_spend_usd" in summary:
        unknown_outcomes = int(summary["unknown_charge_outcomes"])
        unknown_reservation = Decimal(str(summary["unknown_charge_reservation_usd"]))
        budget_accounted = Decimal(str(summary["budget_accounted_run_spend_usd"]))
        maximum = Decimal(str(summary["maximum_run_spend_usd"]))
        remaining = Decimal(str(summary["remaining_run_spend_usd"]))
        internally_consistent = (
            known == Decimal(str(summary["run_spend_usd"]))
            and budget_accounted == known + unknown_reservation
            and remaining == maximum - budget_accounted
        )
        legacy_exception = spec.run_id == "gemini-v3"
        if legacy_exception:
            non_corrections = {row["id"]: row for row in errata["non_corrections"]}
            exception = non_corrections.get("gemini-v3-zero-unknown-charge-reservations", {})
            reservation_reconciles = (
                missing_costs == 1
                and unknown_outcomes == 0
                and unknown_reservation == 0
                and exception.get("decision") == "retain_without_rewrite"
                and exception.get("summary_file_sha256")
                == _file_digest(repository_root / spec.summary_path)
            )
            reservation_basis = "frozen_pre-reservation_revision_disclosed_by_errata"
        else:
            per_request = Decimal(str(plan["caps"]["per_request_theoretical_maximum_usd"]))
            reservation_reconciles = (
                missing_costs == unknown_outcomes
                and unknown_reservation == per_request * unknown_outcomes
            )
            reservation_basis = "per_request_theoretical_maximum"
        record = {
            "known_run_spend_usd": _format_decimal(known),
            "unknown_charge_outcomes": unknown_outcomes,
            "unknown_charge_reservation_usd": _format_decimal(unknown_reservation),
            "budget_accounted_run_spend_usd": _format_decimal(budget_accounted),
            "maximum_run_spend_usd": _format_decimal(maximum),
            "remaining_run_spend_usd": _format_decimal(remaining),
            "transport_rows_without_cost": missing_costs,
            "reservation_basis": reservation_basis,
        }
        return record, internally_consistent and reservation_reconciles

    incremental = Decimal(str(summary["calibration_incremental_spend_usd"]))
    prior_known = Decimal(str(summary["known_prior_aggregate_spend_usd"]))
    prior_reserved = Decimal(str(summary["unknown_prior_charge_reservation_usd"]))
    known_aggregate = Decimal(str(summary["actual_aggregate_spend_usd"]))
    budget_aggregate = Decimal(str(summary["budget_accounted_aggregate_spend_usd"]))
    maximum = Decimal(str(summary["maximum_aggregate_spend_usd"]))
    remaining = Decimal(str(summary["remaining_aggregate_spend_usd"]))
    internally_consistent = (
        missing_costs == 0
        and known == incremental
        and known_aggregate == prior_known + incremental
        and budget_aggregate == prior_known + prior_reserved + incremental
        and remaining == maximum - budget_aggregate
    )
    record = {
        "known_run_spend_usd": _format_decimal(known),
        "unknown_charge_outcomes": 0,
        "unknown_charge_reservation_usd": "0.000000000",
        "budget_accounted_run_spend_usd": _format_decimal(known),
        "maximum_run_spend_usd": None,
        "remaining_run_spend_usd": None,
        "transport_rows_without_cost": missing_costs,
        "prior_unknown_charge_reservation_usd": _format_decimal(prior_reserved),
        "known_aggregate_spend_usd": _format_decimal(known_aggregate),
        "budget_accounted_aggregate_spend_usd": _format_decimal(budget_aggregate),
        "reservation_basis": "legacy_shared_campaign_with_prior_unknown_reservation",
    }
    return record, internally_consistent


def audit_run(
    repository_root: Path,
    spec: RunSpec,
    *,
    errata: dict[str, Any],
) -> dict[str, Any]:
    plan_path = repository_root / spec.plan_path
    summary_path = repository_root / spec.summary_path
    plan = _load_json(plan_path)
    summary = _load_json(summary_path)
    task_order = plan.get("task_order", [])
    results = summary.get("episode_results", [])
    classifications = Counter(str(row["classification"]) for row in results)
    counts = _classification_counts(summary)
    allowed_classifications = set(classifications) <= _CLASSIFICATION_KEYS
    assigned = int(plan["assigned_policy_task_pairs"])
    attempted = len(results)
    slot = str(plan["policy"]["slot"])
    manifest = plan["policy"]["policy_manifest"]
    endpoint_record = plan["policy"].get("live_endpoint_record") or plan["policy"].get(
        "price_record"
    )
    _require(isinstance(endpoint_record, dict), f"missing endpoint record for {spec.run_id}")
    errata_by_path = _errata_records(errata)

    source_binding = True
    if spec.v3_errata_bound:
        for path in (spec.plan_path, spec.summary_path):
            record = errata_by_path.get(path.as_posix())
            source_binding = source_binding and record is not None
            if record is not None:
                source_binding = source_binding and record["file_sha256"] == _file_digest(
                    repository_root / path
                )
        plan_record = errata_by_path.get(spec.plan_path.as_posix(), {})
        source_binding = source_binding and plan_record.get("approved_plan_sha256") == content_digest(
            plan
        )
    elif spec.relation_path is not None:
        relation = _load_json(repository_root / spec.relation_path)
        source_binding = (
            relation["authoritative"]["approved_plan_content_sha256"] == content_digest(plan)
            and relation["authoritative"]["run_summary_file_sha256"]
            == _file_digest(summary_path)
        )

    spend, spend_ok = _spend_record(repository_root, spec, plan, summary, errata)
    summary_classifications = {
        str(key): int(value) for key, value in summary["classifications"].items()
    }
    results_match_order = [row["task_id"] for row in results] == [
        row["task_id"] for row in task_order[:attempted]
    ]
    action_bounds = all(
        0 <= int(result["environment_actions"]) <= int(task["max_episode_steps"])
        for result, task in zip(results, task_order[:attempted], strict=True)
    )
    success_flags = all(
        bool(row["success"]) == (row["classification"] == "success_termination")
        for row in results
    )
    checks = {
        "present_in_main_inventory": spec.summary_path.as_posix() in inventory_main(repository_root),
        "plan_digest_matches_summary": content_digest(plan) == summary["approved_plan_sha256"],
        "source_file_hash_binding": source_binding,
        "assigned_denominator_matches": (
            assigned == int(summary["assigned_policy_task_pairs"]) == len(task_order) == 50
        ),
        "attempted_rows_reconcile": attempted == int(summary["attempted_policy_task_pairs"]),
        "classification_counts_reconcile": (
            allowed_classifications
            and summary_classifications == dict(sorted(classifications.items()))
            and sum(counts[key] for key in counts if key != "attempted") == attempted
        ),
        "success_count_reconciles": (
            success_flags and counts["success"] == int(summary["successful_policy_task_pairs"])
        ),
        "task_order_and_action_bounds_reconcile": results_match_order and action_bounds,
        "provider_request_counts_reconcile": (
            len(summary["transport_records"])
            == int(summary["provider_wire_requests"])
            == int(summary["provider_calls_made"])
        ),
        "spend_and_unknown_reservations_reconcile": spend_ok,
        "redaction_scan_clean": _redaction_is_safe(plan, summary),
        "restricted_journal_excluded": (
            not _is_tracked(repository_root, spec.journal_path)
            and _is_ignored(repository_root, spec.journal_path)
        ),
        "completed_assigned_denominator": (
            attempted == assigned and summary["completed_all_assigned_pairs"] is True
        ),
        "publication_relation_verified": (
            False
            if spec.run_id == "gemini-v3"
            else (
                _validate_old_relation(repository_root, spec)
                if spec.run_id == "qwen-v2"
                else True
            )
        ),
    }
    source_checks_passed = all(checks.values())
    return {
        "run_id": spec.run_id,
        "plan_path": spec.plan_path.as_posix(),
        "summary_path": spec.summary_path.as_posix(),
        "slot": slot,
        "provider_alias": manifest["provider"],
        "model_alias": manifest["model"],
        "source_commit_date": _source_commit_date(repository_root, spec.summary_path),
        "run_execution_date": None,
        "run_execution_date_disclosure": "not recorded in committed plan or summary",
        "endpoint_record_observed_at_utc": endpoint_record["observed_at_utc"],
        "versions": {
            "system_prompt_digest": manifest["system_prompt_digest"],
            "memory_policy_version": manifest["memory_policy_version"],
            "parser_version": manifest["parser_version"],
            "response_schema_version": manifest["response_schema_version"],
            "coordinate_adapter": manifest["coordinate_adapter"],
            "transport_retry_rule": manifest["transport_retry_rule"],
        },
        "assigned_tasks": assigned,
        "classification_counts": counts,
        "spend": spend,
        "stop_conditions": {
            "approved": plan.get("run_continuation", {}).get("hard_stop_conditions", []),
            "consecutive_failure_limit": plan.get("run_continuation", {}).get(
                "consecutive_failure_limit"
            ),
            "tripped": summary.get("run_continuation", {}).get("tripped"),
            "trip_reason": summary.get("run_continuation", {}).get("trip_reason"),
            "observed_stop": (
                "completed_assigned_denominator"
                if attempted == assigned
                else summary.get("run_continuation", {}).get("trip_reason")
                or "approved_legacy_stop"
            ),
        },
        "fault_taxonomy": {
            "stored_taxonomy_preserved": True,
            "legacy_cli_process_failures_recorded_as_invalid_output": 0,
            "basis": "provider alias and stored transport are HTTP/OpenRouter, not a CLI transport",
            "bound": "none of this run's invalid_output rows can be a CLI process failure",
        },
        "checks": checks,
        "source_checks_passed": source_checks_passed,
        "publication_relation": None,
        "published": False,
    }


def _validate_old_relation(repository_root: Path, spec: RunSpec) -> bool:
    assert spec.relation_path is not None
    relation = _load_json(repository_root / spec.relation_path)
    if relation.get("schema_version") != "pixelgym-agent-v5-d56-qwen-publication-relation-v1":
        return False
    if relation.get("redaction_policy_version") != REDACTION_POLICY_VERSION:
        return False
    plan = _load_json(repository_root / spec.plan_path)
    if relation["authoritative"]["approved_plan_content_sha256"] != content_digest(plan):
        return False
    if relation["authoritative"]["run_summary_file_sha256"] != _file_digest(
        repository_root / spec.summary_path
    ):
        return False
    for artifact in ("derivative", "report"):
        target = repository_root / relation["publishable"][f"{artifact}_path"]
        if (
            not target.exists()
            or _file_digest(target)
            != relation["publishable"][f"{artifact}_file_sha256"]
        ):
            return False
    derivative = _load_json(
        repository_root / relation["publishable"]["derivative_path"]
    )
    if content_digest(derivative) != relation["publishable"]["derivative_content_sha256"]:
        return False
    audit_digest = relation["authoritative"]["integrity_audit_file_sha256"]
    audit_path = repository_root / Path(
        "artifacts/grounding-v5-d56-qwen-full-calibration-integrity-audit.json"
    )
    if _file_digest(audit_path) != audit_digest:
        return False
    audit = _load_json(audit_path)
    excluded = relation["excluded_authoritative_artifacts"]
    if len(excluded) != 1:
        return False
    if (
        audit["artifacts"]["attempt_journal"]["sha256"]
        != relation["authoritative"]["restricted_attempt_journal_file_sha256"]
        or audit["artifacts"]["attempt_journal"]["sha256"]
        != relation["excluded_authoritative_artifacts"][0]["sha256"]
    ):
        return False
    return all(
        row["path"] == spec.journal_path.as_posix()
        and row["git_status"] == "must_not_commit"
        and not _is_tracked(repository_root, Path(row["path"]))
        and _is_ignored(repository_root, Path(row["path"]))
        for row in excluded
    )


def _published_row(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: audit[key]
        for key in (
            "run_id",
            "slot",
            "provider_alias",
            "model_alias",
            "source_commit_date",
            "run_execution_date",
            "run_execution_date_disclosure",
            "endpoint_record_observed_at_utc",
            "versions",
            "assigned_tasks",
            "classification_counts",
            "spend",
            "stop_conditions",
            "fault_taxonomy",
            "plan_path",
            "summary_path",
        )
    }


def build_derivative(run_audits: list[dict[str, Any]]) -> dict[str, Any]:
    publishable = [row for row in run_audits if row["source_checks_passed"]]
    _require(
        tuple(row["run_id"] for row in publishable) == EXPECTED_PUBLISHED_RUNS,
        "verified completed-run selection differs from the approved publication set",
    )
    excluded = []
    for row in run_audits:
        if row["source_checks_passed"]:
            continue
        excluded.append(
            {
                "run_id": row["run_id"],
                "slot": row["slot"],
                "classification_counts": row["classification_counts"],
                "failed_checks": sorted(
                    name for name, passed in row["checks"].items() if not passed
                ),
            }
        )
    derivative = {
        "schema_version": DERIVATIVE_SCHEMA_VERSION,
        "purpose": "publish completed D5.6 calibration runs from retained committed evidence",
        "provider_calls_made": 0,
        "status": {
            "benchmark_score": False,
            "milestone_gate_verdict": "not_evaluated_human_owned",
            "calibration_result": "descriptive",
        },
        "date_provenance": {
            "run_execution_dates": "not recorded in committed plans or summaries",
            "source_commit_dates": "derived from immutable git history",
            "endpoint_record_dates": "copied from each approved plan",
        },
        "runs": [_published_row(row) for row in publishable],
        "unpublished_retained_runs": excluded,
        "redaction": {
            "policy_version": REDACTION_POLICY_VERSION,
            "excluded": [
                "raw provider requests and responses",
                "screenshots and screenshot bytes",
                "private policy and environment checkpoints",
                "credentials and absolute operator paths",
                "restricted attempt journals",
            ],
            "public_verification_limit": (
                "The committed summaries expose aggregate transport rows and journal event-chain "
                "digests, but not restricted journals or their file hashes; row-level journal "
                "verification is unavailable from a public clone."
            ),
        },
        "limitations": [
            "Calibration is development evidence, not a benchmark score or milestone-gate verdict.",
            "Gemini v3b reserves USD 1.990656000 for 20 outcomes whose charges are unknown.",
            "Qwen v3 completed 50 assignments with zero exact-success terminations.",
            "The two completed slots use different model/provider routes and are descriptive, not a controlled model comparison.",
            "Execution dates and restricted-journal file hashes were not retained in committed evidence.",
            "The historical classification labels are preserved without reinterpretation.",
        ],
    }
    validate_credential_free(derivative)
    return derivative


def _pct(numerator: int, denominator: int) -> str:
    return "n/a" if denominator == 0 else f"{100 * numerator / denominator:.1f}%"


def render_report(derivative: dict[str, Any], *, derivative_sha256: str) -> str:
    lines = [
        "# Completed D5.6 V5 Calibration Results",
        "",
        "**Status:** descriptive completed calibration evidence; not a benchmark score or milestone-gate verdict",
        "",
        (
            "This report publishes every retained D5.6 full-calibration run that completed its "
            "assigned denominator and passed the committed-evidence checks. It was generated "
            "without provider calls from the response-content-free derivative."
        ),
        "",
        "## Calibration table",
        "",
        "| Policy slot | Assigned | Attempted | Success | Invalid output | Request failure | Infrastructure failure | Policy violation | Truncation |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in derivative["runs"]:
        counts = run["classification_counts"]
        lines.append(
            f"| `{run['slot']}` | {run['assigned_tasks']} | {counts['attempted']} | "
            f"{counts['success']} ({_pct(counts['success'], counts['attempted'])}) | "
            f"{counts['invalid_output']} | {counts['request_failure']} | "
            f"{counts['infrastructure_failure']} | {counts['policy_violation']} | "
            f"{counts['truncation']} |"
        )
    lines.extend(
        [
            "",
            "Null and negative results are shown unchanged. Attempted is kept separate from every terminal classification.",
            "",
            "## Policy identity, dates, spend, and stopping",
            "",
        ]
    )
    for run in derivative["runs"]:
        versions = run["versions"]
        spend = run["spend"]
        stop = run["stop_conditions"]
        lines.extend(
            [
                f"### `{run['slot']}`",
                "",
                f"- Provider/model alias: `{run['provider_alias']}` / `{run['model_alias']}`",
                f"- Execution date: {run['run_execution_date_disclosure']}",
                f"- Endpoint record observed: `{run['endpoint_record_observed_at_utc']}`; source evidence committed: `{run['source_commit_date']}`",
                f"- Prompt digest: `{versions['system_prompt_digest']}`",
                f"- Memory/parser/response policy: `{versions['memory_policy_version']}` / `{versions['parser_version']}` / `{versions['response_schema_version']}`",
                f"- Coordinate/retry policy: `{versions['coordinate_adapter']}` / `{versions['transport_retry_rule']}`",
                f"- Known run spend: `${spend['known_run_spend_usd']}`; unknown-charge reservation: `${spend['unknown_charge_reservation_usd']}` across {spend['unknown_charge_outcomes']} outcomes; budget-accounted run spend: `${spend['budget_accounted_run_spend_usd']}`",
                f"- Observed stop: `{stop['observed_stop']}`; stop guard tripped: `{stop['tripped']}`; trip reason: `{stop['trip_reason']}`",
                f"- Approved hard stops: {', '.join(f'`{item}`' for item in stop['approved'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## Retained runs not published",
            "",
            "| Run | Policy slot | Attempted | Failed publication checks |",
            "|---|---|---:|---|",
        ]
    )
    for run in derivative["unpublished_retained_runs"]:
        lines.append(
            f"| `{run['run_id']}` | `{run['slot']}` | "
            f"{run['classification_counts']['attempted']} | "
            f"{', '.join(f'`{item}`' for item in run['failed_checks'])} |"
        )
    lines.extend(
        [
            "",
            "Gemini v3 stopped after 5 of 50 assignments when its run ledger blocked. The older Qwen v2 run stopped after 13 of 50 assignments under its approved first-invalid-output rule. Neither incomplete run is included in the calibration table.",
            "",
            "## Fault-taxonomy disclosure",
            "",
            (
                "PR #155 changed classification of CLI process failures. These four retained runs "
                "used HTTP/OpenRouter transports, not CLI transports, so the number of "
                "`invalid_output` rows that can be former-taxonomy CLI process failures is zero "
                "for every run. Stored labels were not rewritten or reinterpreted."
            ),
            "",
            "## Evidence and redaction",
            "",
            f"- Report schema: `{REPORT_SCHEMA_VERSION}`",
            f"- Publishable derivative: `{derivative_sha256}`",
            "- [Publishable derivative](grounding-v5-d56-completed-calibrations-publishable.json)",
            "- [Integrity audit](grounding-v5-d56-completed-calibrations-integrity-audit.json)",
            "- [Publication relation](grounding-v5-d56-completed-calibrations-publication-relation.json)",
            "- [Evidence errata](grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json)",
            "- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)",
            "",
            derivative["redaction"]["public_verification_limit"],
            "",
            "The derivative and report contain no response bodies, request bodies, screenshots, checkpoint contents, credentials, or absolute operator paths. Restricted journals remain untracked and ignored.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in derivative["limitations"])
    lines.append("")
    return "\n".join(lines)


def build_relation(
    repository_root: Path,
    run_audits: list[dict[str, Any]],
    *,
    derivative: dict[str, Any],
    derivative_sha256: str,
    report_sha256: str,
) -> dict[str, Any]:
    by_id = {row["run_id"]: row for row in run_audits}
    sources = []
    exclusions = []
    for run_id in EXPECTED_PUBLISHED_RUNS:
        row = by_id[run_id]
        sources.append(
            {
                "run_id": run_id,
                "approved_plan_content_sha256": _load_json(
                    repository_root / Path(row["summary_path"])
                )["approved_plan_sha256"],
                "plan_path": row["plan_path"],
                "plan_file_sha256": _file_digest(repository_root / Path(row["plan_path"])),
                "summary_path": row["summary_path"],
                "summary_file_sha256": _file_digest(
                    repository_root / Path(row["summary_path"])
                ),
                "journal_event_chain_sha256": _load_json(
                    repository_root / Path(row["summary_path"])
                )["journal_integrity"]["event_chain_digest"],
            }
        )
        journal_path = Path(row["summary_path"]).parent / "attempts.sqlite"
        exclusions.append(
            {
                "path": journal_path.as_posix(),
                "sha256": None,
                "size_bytes": None,
                "reason": "restricted raw provider responses, screenshots, and private checkpoints",
                "git_status": "must_not_commit",
                "public_verification_limit": "file hash and size are not recorded in committed v3 evidence",
            }
        )
    relation = {
        "schema_version": RELATION_SCHEMA_VERSION,
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "authoritative": {
            "runs": sources,
            "errata_path": ERRATA_PATH.as_posix(),
            "errata_file_sha256": _file_digest(repository_root / ERRATA_PATH),
        },
        "publishable": {
            "derivative_path": DERIVATIVE_PATH.as_posix(),
            "derivative_content_sha256": content_digest(derivative),
            "derivative_file_sha256": derivative_sha256,
            "report_path": REPORT_PATH.as_posix(),
            "report_file_sha256": report_sha256,
        },
        "excluded_authoritative_artifacts": exclusions,
    }
    validate_credential_free(relation)
    return relation


def validate_relation(repository_root: Path, relation: dict[str, Any]) -> bool:
    if relation.get("schema_version") != RELATION_SCHEMA_VERSION:
        return False
    if relation.get("redaction_policy_version") != REDACTION_POLICY_VERSION:
        return False
    if _file_digest(repository_root / ERRATA_PATH) != relation["authoritative"].get(
        "errata_file_sha256"
    ):
        return False
    if [row["run_id"] for row in relation["authoritative"]["runs"]] != list(
        EXPECTED_PUBLISHED_RUNS
    ):
        return False
    for source in relation["authoritative"]["runs"]:
        plan_path = repository_root / source["plan_path"]
        summary_path = repository_root / source["summary_path"]
        summary = _load_json(summary_path)
        if (
            _file_digest(plan_path) != source["plan_file_sha256"]
            or _file_digest(summary_path) != source["summary_file_sha256"]
            or content_digest(_load_json(plan_path))
            != source["approved_plan_content_sha256"]
            or summary["approved_plan_sha256"]
            != source["approved_plan_content_sha256"]
            or summary["journal_integrity"]["event_chain_digest"]
            != source["journal_event_chain_sha256"]
        ):
            return False
    for key in ("derivative", "report"):
        path = repository_root / relation["publishable"][f"{key}_path"]
        if _file_digest(path) != relation["publishable"][f"{key}_file_sha256"]:
            return False
    derivative = _load_json(repository_root / relation["publishable"]["derivative_path"])
    if content_digest(derivative) != relation["publishable"]["derivative_content_sha256"]:
        return False
    validate_credential_free(relation)
    return all(
        row["git_status"] == "must_not_commit"
        and row["sha256"] is None
        and row["size_bytes"] is None
        and not _is_tracked(repository_root, Path(row["path"]))
        and _is_ignored(repository_root, Path(row["path"]))
        for row in relation["excluded_authoritative_artifacts"]
    )


def _attach_relation_checks(
    repository_root: Path,
    run_audits: list[dict[str, Any]],
    relation: dict[str, Any],
) -> None:
    new_relation_ok = validate_relation(repository_root, relation)
    for row, spec in zip(run_audits, RUN_SPECS, strict=True):
        if spec.run_id == "qwen-v2":
            relation_ok = _validate_old_relation(repository_root, spec)
            relation_path = spec.relation_path
        elif spec.run_id == "gemini-v3":
            relation_ok = False
            relation_path = None
        else:
            relation_ok = new_relation_ok
            relation_path = RELATION_PATH
        row["checks"]["publication_relation_verified"] = relation_ok
        row["publication_relation"] = {
            "path": None if relation_path is None else relation_path.as_posix(),
            "verified": relation_ok,
        }
        row["published"] = all(row["checks"].values())


def build_audit(
    repository_root: Path,
    run_audits: list[dict[str, Any]],
    relation: dict[str, Any],
) -> dict[str, Any]:
    _attach_relation_checks(repository_root, run_audits, relation)
    published = [row["run_id"] for row in run_audits if row["published"]]
    _require(tuple(published) == EXPECTED_PUBLISHED_RUNS, "final publication checks changed run set")
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "provider_calls_made": 0,
        "main_inventory_command": "git ls-tree -r --name-only main -- artifacts",
        "main_full_calibration_summaries": list(inventory_main(repository_root)),
        "runs": run_audits,
        "publication": {
            "published_run_ids": published,
            "checks_failed_for_published_runs": 0,
            "milestone_gate_verdict": "not_evaluated_human_owned",
        },
        "relation_file_sha256": _file_digest(repository_root / RELATION_PATH),
        "derivative_file_sha256": _file_digest(repository_root / DERIVATIVE_PATH),
        "report_file_sha256": _file_digest(repository_root / REPORT_PATH),
    }


def _write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace publication artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _source_audits(repository_root: Path) -> list[dict[str, Any]]:
    errata = _load_json(repository_root / ERRATA_PATH)
    _require(errata["provider_calls_made"] == 0, "errata unexpectedly records provider calls")
    return [audit_run(repository_root, spec, errata=errata) for spec in RUN_SPECS]


def publish(repository_root: Path) -> dict[str, Any]:
    outputs = (DERIVATIVE_PATH, REPORT_PATH, RELATION_PATH, AUDIT_PATH)
    for relative in outputs:
        if (repository_root / relative).exists():
            raise FileExistsError(f"refusing to replace publication artifact: {relative}")
    run_audits = _source_audits(repository_root)
    derivative = build_derivative(run_audits)
    derivative_path = repository_root / DERIVATIVE_PATH
    _write_new(derivative_path, json.dumps(derivative, indent=2, sort_keys=True) + "\n")
    derivative_sha256 = _file_digest(derivative_path)
    report_path = repository_root / REPORT_PATH
    _write_new(
        report_path,
        render_report(derivative, derivative_sha256=derivative_sha256),
    )
    relation = build_relation(
        repository_root,
        run_audits,
        derivative=derivative,
        derivative_sha256=derivative_sha256,
        report_sha256=_file_digest(report_path),
    )
    relation_path = repository_root / RELATION_PATH
    _write_new(relation_path, json.dumps(relation, indent=2, sort_keys=True) + "\n")
    audit = build_audit(repository_root, run_audits, relation)
    audit_path = repository_root / AUDIT_PATH
    _write_new(audit_path, json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return {
        "provider_calls_made": 0,
        "published_run_ids": list(EXPECTED_PUBLISHED_RUNS),
        "artifacts": {
            relative.name: _file_digest(repository_root / relative) for relative in outputs
        },
    }


def verify(repository_root: Path) -> dict[str, Any]:
    derivative = _load_json(repository_root / DERIVATIVE_PATH)
    relation = _load_json(repository_root / RELATION_PATH)
    audit = _load_json(repository_root / AUDIT_PATH)
    run_audits = _source_audits(repository_root)
    expected_derivative = build_derivative(run_audits)
    _require(derivative == expected_derivative, "published derivative is not reproducible")
    expected_report = render_report(
        derivative,
        derivative_sha256=_file_digest(repository_root / DERIVATIVE_PATH),
    )
    _require(
        (repository_root / REPORT_PATH).read_text(encoding="utf-8") == expected_report,
        "published report is not reproducible",
    )
    expected_relation = build_relation(
        repository_root,
        run_audits,
        derivative=derivative,
        derivative_sha256=_file_digest(repository_root / DERIVATIVE_PATH),
        report_sha256=_file_digest(repository_root / REPORT_PATH),
    )
    _require(relation == expected_relation, "publication relation is not reproducible")
    expected_audit = build_audit(repository_root, run_audits, relation)
    _require(audit == expected_audit, "integrity audit is not reproducible")
    verification_rows = []
    for row in expected_audit["runs"]:
        plan_path = repository_root / Path(row["plan_path"])
        summary_path = repository_root / Path(row["summary_path"])
        summary = _load_json(summary_path)
        verification_rows.append(
            {
                "run_id": row["run_id"],
                "slot": row["slot"],
                "provider_alias": row["provider_alias"],
                "model_alias": row["model_alias"],
                "assigned_tasks": row["assigned_tasks"],
                "classification_counts": row["classification_counts"],
                "plan_summary_binding": {
                    "computed_plan_content_sha256": content_digest(_load_json(plan_path)),
                    "summary_approved_plan_sha256": summary["approved_plan_sha256"],
                    "plan_file_sha256": _file_digest(plan_path),
                    "summary_file_sha256": _file_digest(summary_path),
                },
                "dates": {
                    "run_execution_date": row["run_execution_date"],
                    "run_execution_date_disclosure": row[
                        "run_execution_date_disclosure"
                    ],
                    "endpoint_record_observed_at_utc": row[
                        "endpoint_record_observed_at_utc"
                    ],
                    "source_commit_date": row["source_commit_date"],
                },
                "versions": row["versions"],
                "spend": row["spend"],
                "stop_conditions": row["stop_conditions"],
                "fault_taxonomy": row["fault_taxonomy"],
                "publication_relation": row["publication_relation"],
                "checks": row["checks"],
                "published": row["published"],
            }
        )
    return {
        "provider_calls_made": 0,
        "main_full_calibration_summaries": list(inventory_main(repository_root)),
        "runs": verification_rows,
        "publication": expected_audit["publication"],
        "artifacts_reproducible": True,
    }


def render_verification(result: dict[str, Any]) -> str:
    """Render compact raw evidence suitable for review without dropping checked fields."""

    lines = [
        "provider_calls_made=0",
        "main_full_calibration_summaries="
        + json.dumps(result["main_full_calibration_summaries"], separators=(",", ":")),
    ]
    for row in result["runs"]:
        counts = row["classification_counts"]
        binding = row["plan_summary_binding"]
        dates = row["dates"]
        spend = row["spend"]
        versions = row["versions"]
        stop = row["stop_conditions"]
        taxonomy = row["fault_taxonomy"]
        relation = row["publication_relation"]
        lines.extend(
            [
                (
                    f"RUN {row['run_id']} slot={row['slot']} provider={row['provider_alias']} "
                    f"model={row['model_alias']} assigned={row['assigned_tasks']} "
                    f"attempted={counts['attempted']} invalid_output={counts['invalid_output']} "
                    f"request_failure={counts['request_failure']} "
                    f"infrastructure_failure={counts['infrastructure_failure']} "
                    f"policy_violation={counts['policy_violation']} "
                    f"truncation={counts['truncation']} success={counts['success']}"
                ),
                (
                    f"DIGEST {row['run_id']} computed_plan={binding['computed_plan_content_sha256']} "
                    f"summary_plan={binding['summary_approved_plan_sha256']} "
                    f"plan_file={binding['plan_file_sha256']} "
                    f"summary_file={binding['summary_file_sha256']}"
                ),
                (
                    f"SPEND {row['run_id']} known_run_usd={spend['known_run_spend_usd']} "
                    f"unknown_outcomes={spend['unknown_charge_outcomes']} "
                    f"unknown_reservation_usd={spend['unknown_charge_reservation_usd']} "
                    f"budget_accounted_run_usd={spend['budget_accounted_run_spend_usd']} "
                    f"reservation_basis={spend['reservation_basis']}"
                ),
                (
                    f"DATES {row['run_id']} execution={dates['run_execution_date']} "
                    f"execution_disclosure={json.dumps(dates['run_execution_date_disclosure'])} "
                    f"endpoint_observed={dates['endpoint_record_observed_at_utc']} "
                    f"source_commit={dates['source_commit_date']}"
                ),
                (
                    f"VERSIONS {row['run_id']} prompt={versions['system_prompt_digest']} "
                    f"memory={versions['memory_policy_version']} parser={versions['parser_version']} "
                    f"response={versions['response_schema_version']} "
                    f"coordinate={versions['coordinate_adapter']} "
                    f"retry={versions['transport_retry_rule']}"
                ),
                (
                    f"STOP {row['run_id']} observed={stop['observed_stop']} "
                    f"tripped={stop['tripped']} trip_reason={stop['trip_reason']} "
                    f"approved={json.dumps(stop['approved'], separators=(',', ':'))}"
                ),
                (
                    f"TAXONOMY {row['run_id']} preserved={taxonomy['stored_taxonomy_preserved']} "
                    "legacy_cli_process_failures_recorded_as_invalid_output="
                    f"{taxonomy['legacy_cli_process_failures_recorded_as_invalid_output']} "
                    f"bound={json.dumps(taxonomy['bound'])}"
                ),
                (
                    f"RELATION {row['run_id']} path={relation['path']} "
                    f"verified={relation['verified']}"
                ),
                (
                    f"CHECKS {row['run_id']} "
                    + json.dumps(row["checks"], sort_keys=True, separators=(",", ":"))
                ),
                f"PUBLICATION {row['run_id']} published={row['published']}",
            ]
        )
    lines.append(
        "RESULT artifacts_reproducible=True published_run_ids="
        + json.dumps(result["publication"]["published_run_ids"], separators=(",", ":"))
        + f" checks_failed_for_published_runs={result['publication']['checks_failed_for_published_runs']}"
        + f" milestone_gate_verdict={result['publication']['milestone_gate_verdict']}"
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify existing generated artifacts instead of creating them",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.verify:
        print(render_verification(verify(root)))
    else:
        print(json.dumps(publish(root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
