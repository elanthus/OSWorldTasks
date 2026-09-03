from __future__ import annotations

import json
from pathlib import Path

from scripts.publish_grounding_v5_d56_qwen_full_calibration import (
    _file_digest,
    _format_cost,
    render_report,
)

ROOT = Path(__file__).parents[2]
AUDIT = ROOT / "artifacts/grounding-v5-d56-qwen-full-calibration-integrity-audit.json"
DERIVATIVE = ROOT / "artifacts/grounding-v5-d56-qwen-full-calibration-publishable.json"
RELATION = ROOT / "artifacts/grounding-v5-d56-qwen-full-calibration-publication-relation.json"
REPORT = ROOT / "artifacts/grounding-v5-d56-qwen-full-calibration-report.md"


def _keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            result.add(str(key))
            result.update(_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            result.update(_keys(nested))
    return result


def test_qwen_integrity_audit_is_no_call_and_has_no_failed_checks() -> None:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))

    assert audit["provider_calls_made"] == 0
    assert audit["result"] == {
        "checks_failed": 0,
        "checks_verified": 8,
        "milestone_gate_verdict": "not_evaluated_human_owned",
        "provider_calls_made": 0,
    }
    assert all(check["verified"] is True for check in audit["checks"])
    checks = {check["name"]: check for check in audit["checks"]}
    assert checks["assignment_and_episode_reconciliation"][
        "step_limit_horizons_exhausted"
    ] == 12
    assert checks["terminal_invalid_output_route"] == {
        "attempt_index": 0,
        "environment_actions": 9,
        "failure_code": "parse_failure",
        "model_attempts": 10,
        "name": "terminal_invalid_output_route",
        "parser_version": "pixelgym-agent-v5-json-action-normalized-1000x1000-parser-v1",
        "provider_wire_requests": 10,
        "response_structure": {
            "begins_with_opening_brace": True,
            "closing_brace_count": 0,
            "completion_tokens": 192,
            "content_characters": 504,
            "finish_reason": "stop",
            "meaningful_characters": 56,
            "opening_brace_count": 1,
            "trailing_json_whitespace_characters": 448,
        },
        "retry_permitted_by_approved_plan": False,
        "sanitized_reason": "JSONDecodeError",
        "step_index": 9,
        "task_id": "v5-2d305fda4e9ebe2d9075a384",
        "trial_id": "d56-qwen-v2-12-v5-2d305fda4e9ebe2d9075a384",
        "verified": True,
    }


def test_qwen_derivative_reconciles_negative_result_and_excludes_payloads() -> None:
    derivative = json.loads(DERIVATIVE.read_text(encoding="utf-8"))

    assert derivative["coverage"] == {
        "assigned_tasks": 50,
        "attempted_tasks": 13,
        "completed_all_assigned_tasks": False,
        "invalid_outputs": 1,
        "step_limit_truncations": 12,
        "successful_tasks": 0,
        "unattempted_tasks": 37,
    }
    assert derivative["provider_requests"]["wire_requests"] == 350
    assert derivative["provider_requests"]["completed_responses"] == 350
    assert derivative["provider_requests"]["unknown_outcomes"] == 0
    assert derivative["provider_requests"]["retryable_rate_limits"] == 0
    assert derivative["cost"]["known_calibration_incremental_spend_usd"] == "0.125865961"
    assert derivative["cost"]["known_actual_aggregate_spend_usd"] == "4.678631918"
    assert derivative["cost"]["budget_accounted_aggregate_spend_usd"] == "4.778164718"
    assert len(derivative["tasks"]) == 50
    assert sum(task["attempted"] for task in derivative["tasks"]) == 13
    assert (
        derivative["failure_routes"]["invalid_output"]["response_structure"]
        == {
            "begins_with_opening_brace": True,
            "closing_brace_count": 0,
            "completion_tokens": 192,
            "content_characters": 504,
            "finish_reason": "stop",
            "meaningful_characters": 56,
            "opening_brace_count": 1,
            "trailing_json_whitespace_characters": 448,
        }
    )
    assert not {
        "content",
        "idempotency_key",
        "provider_endpoint_identity",
        "request_digest",
        "response_id",
        "screenshot_digest",
    } & _keys(derivative)
    encoded = json.dumps(derivative, sort_keys=True)
    assert "/Users/" not in encoded
    assert "data:image/" not in encoded


def test_qwen_report_is_reproducible_from_publishable_derivative_only() -> None:
    derivative = json.loads(DERIVATIVE.read_text(encoding="utf-8"))

    rendered = render_report(derivative, derivative_sha256=_file_digest(DERIVATIVE))

    assert rendered == REPORT.read_text(encoding="utf-8")
    assert "0 / 13 attempted (0.0%)" in rendered
    assert "not a benchmark score or milestone-gate verdict" in rendered
    assert "raw text remains restricted" in rendered


def test_qwen_publication_relation_excludes_restricted_journal() -> None:
    relation = json.loads(RELATION.read_text(encoding="utf-8"))

    assert relation["excluded_authoritative_artifacts"] == [
        {
            "git_status": "must_not_commit",
            "path": "artifacts/grounding-v5-d56-qwen-full-calibration-run/attempts.sqlite",
            "reason": "restricted provider responses, screenshots, and private checkpoints",
            "sha256": "sha256:81e9137cb5c5074c3a0fd02f8f20ef7c2368ede9dbf95f1a92a1805a61818671",
            "size_bytes": 118_493_184,
        }
    ]
    assert relation["authoritative"]["approved_plan_content_sha256"] == (
        "sha256:fc1f41d00df8c847d55765ea6af6b4c688463a893281f995f3eee3b770c43f7c"
    )
    assert relation["publishable"]["derivative_file_sha256"] == _file_digest(DERIVATIVE)
    assert relation["publishable"]["report_file_sha256"] == _file_digest(REPORT)


def test_qwen_report_formatter_preserves_unknown_spend() -> None:
    assert _format_cost("unknown") == "unknown"
