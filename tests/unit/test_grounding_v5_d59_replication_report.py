"""Offline regression checks for the replication audit and frozen result analysis."""

from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_replication import phase_jobs
from scripts import analyze_grounding_v5_d59_replication as analysis
from scripts import report_grounding_v5_d59_replication as report
from scripts.generate_d510_evidence_report import scan_directory


def summary_and_jobs():
    plan = report.read(report.PLAN)
    jobs = phase_jobs(plan, "pilot")
    public = report.read(report.OUTPUT / "pilot-report.json")
    rows = {r["trial_id"]: r for r in public["results"]}
    return {
        **public,
        "attempted": len(jobs),
        "error": None,
        "results": [{**j, **rows[j["trial_id"]]} for j in jobs],
    }, jobs


def test_failed_pilot_tasks_stay_in_denominator():
    summary, jobs = summary_and_jobs()
    report.validate_rows(summary, jobs)
    assert summary["completed"] == 4
    assert sum(r["success"] for r in summary["results"]) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "unplanned",
        "mode",
        "success",
        "negative_count",
        "wire_count",
        "open_process",
        "counts",
    ],
)
def test_projection_rejects_inconsistent_assignments(mutation):
    summary, jobs = summary_and_jobs()
    row = summary["results"][0]
    if mutation == "missing":
        summary["results"].pop()
    elif mutation == "duplicate":
        summary["results"][-1] = copy.deepcopy(row)
    elif mutation == "unplanned":
        row["trial_id"] = "another-phase-pilot"
    elif mutation == "mode":
        row["mode"] = "wrong"
    elif mutation == "success":
        row["success"] = True
    elif mutation == "negative_count":
        row["model_attempts"] = -1
    elif mutation == "wire_count":
        row["provider_wire_requests"] += 1
    elif mutation == "open_process":
        summary["subprocesses_closed"] = False
    elif mutation == "counts":
        summary["counts"] = {}
    with pytest.raises(ValueError):
        report.validate_rows(summary, jobs)


def timeout_fixture():
    def event(kind, index=0, **payload):
        return SimpleNamespace(
            kind=kind, step_index=21, attempt_index=index, trial_id="timeout-trial", payload=payload
        )

    first = event("attempt_started", idempotency_key="first", request_digest="same-request")
    events = [
        first,
        event("unknown_outcome_infrastructure_failure"),
        event("retryable_transport_fault", next_attempt_permitted=True, bounded_retry_budget=1),
        event("attempt_started", 1, idempotency_key="retry", request_digest="same-request"),
        event("attempt_completed", 1),
    ]
    outcome = {
        "process_confirmed_stopped": True,
        "cli_fault": {
            "code": "claude_process_timeout",
            "phase": "post_send",
            "cost_knowledge": "unknown",
            "model_attempt_consumption": "unknown",
        },
    }
    return first, events, outcome


def test_timeout_is_disclosed_unknown_and_both_attempts_are_counted():
    first, events, outcome = timeout_fixture()
    result = report.audit_timeout("timeout", outcome, first, events)
    assert result["provider_completion"] == result["provider_usage"] == "unknown"
    assert result["counted_attempts_for_action"] == 2
    assert result["invocation_key_digest"] == content_digest("first")


@pytest.mark.parametrize(
    "mutation",
    [
        "not_stopped",
        "other_status",
        "no_retry",
        "changed_request",
        "retry_not_completed",
        "no_permission",
        "extra_retry",
    ],
)
def test_timeout_exception_cannot_hide_an_unverified_or_unbounded_retry(mutation):
    first, events, outcome = timeout_fixture()
    status = "timeout"
    if mutation == "not_stopped":
        outcome["process_confirmed_stopped"] = False
    elif mutation == "other_status":
        status = "failed"
    elif mutation == "no_retry":
        events.pop(3)
    elif mutation == "changed_request":
        events[3].payload["request_digest"] = "different-request"
    elif mutation == "retry_not_completed":
        events.pop()
    elif mutation == "no_permission":
        events[2].payload["next_attempt_permitted"] = False
    elif mutation == "extra_retry":
        third = copy.deepcopy(events[3])
        third.attempt_index = 2
        events.append(third)
    with pytest.raises(ValueError):
        report.audit_timeout(status, outcome, first, events)


def test_frozen_analysis_matches_stored_outcomes_and_keeps_campaigns_separate():
    generated = analysis.build()
    for name, data in generated.items():
        assert (report.OUTPUT / name).read_text() == data
    result = json.loads(generated["confirmatory-analysis.json"])
    confirmatory = result["confirmatory"]
    table = confirmatory["paired_success_table"]
    assert (
        table["pairs"],
        table["both_success"],
        table["history_only_success"],
        table["stateless_only_success"],
        table["both_failure"],
    ) == (192, 5, 57, 15, 115)
    # Independent arithmetic oracle for the exact two-sided binomial tail.
    assert table["exact_mcnemar_two_sided_p"] == pytest.approx(
        2 * sum(math.comb(72, k) for k in range(16)) / 2**72
    )
    boot = confirmatory["paired_absolute_difference"]
    assert boot["estimate"] == 42 / 192
    assert (boot["rows"], boot["clusters"], boot["strata"]) == (192, 168, 6)
    assert (boot["seed"], boot["resamples"]) == (20260911, 10000)
    assert boot["interval"] == pytest.approx([0.13846153846153847, 0.3])
    comparison = result["historical_comparison"]
    assert comparison["historical_paired_absolute_difference"] == 91 / 192
    assert comparison["absolute_difference_between_estimates"] == pytest.approx(49 / 192)
    assert comparison["exceeds_threshold"] is True
    for mode in ("history", "stateless"):
        assert result["success_rates"]["policies"][mode]["overall"]["denominator"] == 192
        assert result["reliability"]["policies"][mode]["reliability_phase_only"] == {
            **result["reliability"]["policies"][mode]["reliability_phase_only"],
            "successes": 3,
            "denominator": 24,
        }
    assert result["human_gate"]["D5.10"] is None
    assert result["disclosure"]["unresolved_invocations"] == 1


def test_response_free_export_and_usage_do_not_impute_the_timeout():
    projection = report.read(report.OUTPUT / "report.json")
    assert len(projection["results"]) == 432
    assert projection["provider_processes_started"] == 8760
    assert sum(r["attempts_with_reported_usage"] for r in projection["results"]) == 8759
    assert projection["invocation_statuses_by_worker"][1]["timeout"] == 1
    assert projection["unresolved_timeouts"][0]["trial_id"] == "d59-rep-primary-6180-history-r0"
    for row in projection["results"]:
        assert "content" not in row and "response" not in row and "raw_stdout" not in row
    scan = scan_directory(report.OUTPUT)
    assert not any(scan["finding_counts"].values()), scan
    diagnostics = analysis.secondary(projection["results"])
    assert diagnostics["primary"]["history"]["attempts_without_reported_usage"] == 1
    assert diagnostics["primary"]["stateless"]["attempts_without_reported_usage"] == 0


@pytest.mark.parametrize(
    "tamper, message",
    [
        ("route", "runtime identity mismatch"),
        ("caps", "bound cap mismatch"),
        ("source", "bound source mismatch"),
        ("profile", "same-credential Max proof mismatch"),
    ],
)
def test_audit_rejects_identity_or_binding_changes_before_reading_journals(
    tmp_path, tamper, message
):
    plan = report.read(report.PLAN)
    # A public, credential-free binding fixture. The altered binding is rehashed so
    # a superficial digest check cannot substitute for checking its frozen semantics.
    from pixelgym.grounding.v5.d59_replication import expected_approval

    approval = expected_approval(plan, "full")
    binding = {
        "execution_plan_digest": content_digest(plan),
        "owner_approval_digest": content_digest(approval),
        "runtime_identity": copy.deepcopy(plan["runtime_identity"]),
        "approved_caps": copy.deepcopy(approval["approved_caps"]),
        "source_files": copy.deepcopy(plan["source_binding"]["files"]),
        "sandbox_probe": {"passed": True},
        "subscription_verification": {
            "http_status": 200,
            "organization_type": "claude_max",
            "same_authentication_as_cli": True,
            "model_requests": 0,
        },
    }
    if tamper == "route":
        binding["runtime_identity"]["auth_method"] = "claude.ai"
    elif tamper == "caps":
        binding["approved_caps"]["model_attempt_cap"] += 1
    elif tamper == "source":
        binding["source_files"] = {}
    elif tamper == "profile":
        binding["subscription_verification"]["same_authentication_as_cli"] = False
    summary = {
        "phase": "full",
        "execution_plan_digest": plan["execution_plan_digest"],
        "execution_binding_digest": content_digest(binding),
        "source_revision": plan["source_binding"]["source_revision"],
        "stop_rule_version": plan["stop_rule"]["version"],
    }
    for name, value in (
        ("execution-plan.json", plan),
        ("summary.json", summary),
        ("execution-live-binding.json", binding),
        ("owner-approval.json", approval),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    with pytest.raises(ValueError, match=message):
        report.audit(tmp_path, tmp_path / "absent-archive", tmp_path / "absent-backup")
