from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from scripts.publish_grounding_v5_d56_completed_calibrations import (
    AUDIT_PATH,
    DERIVATIVE_PATH,
    ERRATA_PATH,
    EXPECTED_MAIN_SUMMARIES,
    RELATION_PATH,
    REPORT_PATH,
    RUN_SPECS,
    PublicationError,
    _file_digest,
    _load_json,
    _redaction_is_safe,
    _source_audits,
    _spend_record,
    build_derivative,
    render_report,
    validate_relation,
    verify,
)

ROOT = Path(__file__).parents[2]


def _keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            result.add(str(key))
            result.update(_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_keys(child))
    return result


def test_publication_is_reproducible_without_provider_calls() -> None:
    result = verify(ROOT)

    assert result["provider_calls_made"] == 0
    assert result["artifacts_reproducible"] is True
    assert tuple(result["main_full_calibration_summaries"]) == EXPECTED_MAIN_SUMMARIES
    assert result["publication"] == {
        "checks_failed_for_published_runs": 0,
        "milestone_gate_verdict": "not_evaluated_human_owned",
        "published_run_ids": ["gemini-v3b", "qwen-v3"],
    }


def test_completed_table_preserves_all_terminal_classifications_and_spend() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    by_run = {row["run_id"]: row for row in derivative["runs"]}

    assert tuple(by_run) == ("gemini-v3b", "qwen-v3")
    assert by_run["gemini-v3b"]["classification_counts"] == {
        "attempted": 50,
        "infrastructure_failure": 0,
        "invalid_output": 1,
        "policy_violation": 0,
        "request_failure": 1,
        "success": 35,
        "truncation": 13,
    }
    assert by_run["gemini-v3b"]["spend"] == {
        "budget_accounted_run_spend_usd": "6.901905750",
        "known_run_spend_usd": "4.911249750",
        "maximum_run_spend_usd": "7.000000000",
        "remaining_run_spend_usd": "0.098094250",
        "reservation_basis": "per_request_theoretical_maximum",
        "transport_rows_without_cost": 20,
        "unknown_charge_outcomes": 20,
        "unknown_charge_reservation_usd": "1.990656000",
    }
    assert by_run["qwen-v3"]["classification_counts"] == {
        "attempted": 50,
        "infrastructure_failure": 0,
        "invalid_output": 3,
        "policy_violation": 0,
        "request_failure": 0,
        "success": 0,
        "truncation": 47,
    }
    assert by_run["qwen-v3"]["spend"]["unknown_charge_reservation_usd"] == (
        "0.000000000"
    )


def test_incomplete_runs_are_excluded_with_failed_checks_disclosed() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    excluded = {row["run_id"]: row for row in derivative["unpublished_retained_runs"]}

    assert excluded["qwen-v2"]["classification_counts"]["attempted"] == 13
    assert excluded["qwen-v2"]["failed_checks"] == ["completed_assigned_denominator"]
    assert excluded["gemini-v3"]["classification_counts"]["attempted"] == 5
    assert excluded["gemini-v3"]["failed_checks"] == [
        "completed_assigned_denominator",
        "publication_relation_verified",
    ]


def test_a_failed_completed_run_cannot_enter_the_derivative() -> None:
    audits = _source_audits(ROOT)
    candidate = next(row for row in audits if row["run_id"] == "gemini-v3b")
    candidate["checks"]["spend_and_unknown_reservations_reconcile"] = False
    candidate["source_checks_passed"] = False

    with pytest.raises(PublicationError, match="publication set"):
        build_derivative(audits)


def test_unknown_reservation_mismatch_is_detected() -> None:
    spec = next(spec for spec in RUN_SPECS if spec.run_id == "gemini-v3b")
    plan = _load_json(ROOT / spec.plan_path)
    summary = copy.deepcopy(_load_json(ROOT / spec.summary_path))
    errata = _load_json(ROOT / ERRATA_PATH)
    summary["unknown_charge_reservation_usd"] = "0"

    _, reconciles = _spend_record(ROOT, spec, plan, summary, errata)

    assert reconciles is False


def test_relation_binds_sources_and_excludes_both_restricted_journals() -> None:
    relation = _load_json(ROOT / RELATION_PATH)

    assert validate_relation(ROOT, relation) is True
    assert [row["run_id"] for row in relation["authoritative"]["runs"]] == [
        "gemini-v3b",
        "qwen-v3",
    ]
    exclusions = relation["excluded_authoritative_artifacts"]
    assert [row["path"] for row in exclusions] == [
        "artifacts/grounding-v5-d56-gemini-v3b-full-calibration-run/attempts.sqlite",
        "artifacts/grounding-v5-d56-qwen-v3-full-calibration-run/attempts.sqlite",
    ]
    assert all(row["git_status"] == "must_not_commit" for row in exclusions)
    assert all(row["sha256"] is None and row["size_bytes"] is None for row in exclusions)
    for row in exclusions:
        assert subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", "--", row["path"]],
            cwd=ROOT,
            check=False,
        ).returncode == 0
        assert subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", row["path"]],
            cwd=ROOT,
            check=False,
            capture_output=True,
        ).returncode != 0


def test_outputs_are_redacted_and_report_is_derivative_only() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    rendered = render_report(derivative, derivative_sha256=_file_digest(ROOT / DERIVATIVE_PATH))
    calibration_table = rendered.split("## Calibration table", 1)[1].split(
        "## Policy identity", 1
    )[0]

    assert rendered == (ROOT / REPORT_PATH).read_text(encoding="utf-8")
    assert calibration_table.count("| `A-gemini-stateful-v3` |") == 1
    assert calibration_table.count("| `B-qwen-stateful-v3` |") == 1
    assert "35 (70.0%)" in rendered
    assert "0 (0.0%)" in rendered
    forbidden = {
        "checkpoint",
        "content",
        "image",
        "prompt",
        "raw_request",
        "raw_response",
        "request_body",
        "response_body",
        "screenshot",
    }
    for path in (DERIVATIVE_PATH, RELATION_PATH, AUDIT_PATH, REPORT_PATH):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "Bearer " not in text
        assert "data:image/" not in text
    assert not (forbidden & _keys(derivative))


def test_redaction_scan_rejects_payloads_paths_and_credentials() -> None:
    assert _redaction_is_safe({"safe": True}, {"content": "provider text"}) is False
    assert _redaction_is_safe({"safe": True}, {"path": "/Users/operator/run"}) is False
    assert _redaction_is_safe({"safe": True}, {"value": "Bearer abcdefghijklmnop"}) is False


def test_readme_numbers_trace_to_generated_derivative() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## v5 agent benchmark (in progress)", 1)[1].split(
        "## Architecture", 1
    )[0]

    assert "35 (70.0%) | 1 | 1 | 0 | 0 | 13 | $1.990656000 (20 outcomes)" in section
    assert "0 (0.0%) | 3 | 0 | 0 | 0 | 47 | $0.000000000 (0 outcomes)" in section
    assert "no v5 result is a benchmark score or a\nmilestone-gate verdict" in section
    assert "`A-gemini-stateful-v2` calibration remains **withdrawn**" in section
    assert DERIVATIVE_PATH.as_posix() in section
