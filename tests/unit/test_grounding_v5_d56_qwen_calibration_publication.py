from __future__ import annotations

import json
from pathlib import Path

from scripts.publish_grounding_v5_d56_qwen_full_calibration import (
    _file_digest,
    _format_cost,
    _journal_projection,
    build_derivative,
    render_report,
)

from pixelgym.grounding.v5.contracts import AttemptIdentity
from pixelgym.grounding.v5.journal import V5AttemptJournal

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


def test_generated_derivative_and_report_separate_failure_counts(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "mixed-outcomes.sqlite"
    journal = V5AttemptJournal(journal_path)
    classifications = (
        "infrastructure_failure",
        "policy_violation",
        "invalid_output",
    )
    task_ids = tuple(f"task-{index}" for index in range(len(classifications)))
    trial_ids = tuple(f"trial-{index}" for index in range(len(classifications)))
    idempotency_keys = tuple(
        f"journal-attempt-{index}" for index in range(len(classifications))
    )
    for index, (classification, trial_id, idempotency_key) in enumerate(
        zip(classifications, trial_ids, idempotency_keys, strict=True)
    ):
        identity = AttemptIdentity(trial_id, 0, 0)
        journal.record_attempt_started(
            identity,
            provider_endpoint_identity="https://provider.invalid",
            request_digest="sha256:" + str(index) * 64,
            idempotency_key=idempotency_key,
            model_attempt_reservation=1,
            control_request_reservation=0,
            pre_call_checkpoint=b"{}",
        )
        if classification == "invalid_output":
            journal.persist_canonical_response(
                identity,
                {
                    "response_id": "synthetic-malformed-response",
                    "model": "synthetic-model",
                    "content": "{",
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
            journal.append_event(
                event_key=f"{identity.key}/sealed_parser_failure",
                kind="sealed_unsuccessful_result",
                trial_id=trial_id,
                step_index=0,
                attempt_index=0,
                payload={
                    "failure_code": "parse_failure",
                    "sanitized_reason": "JSONDecodeError",
                    "parser_version": "synthetic-parser-v1",
                },
            )
        else:
            journal.append_event(
                event_key=f"{identity.key}/sealed_{classification}",
                kind="sealed_unsuccessful_result",
                trial_id=trial_id,
                step_index=0,
                attempt_index=0,
                payload={"failure_code": classification},
            )
    journal.close()

    task_order = [
        {
            "ordinal": index,
            "task_id": task_id,
            "family": "synthetic_family",
            "max_episode_steps": 1,
        }
        for index, task_id in enumerate(task_ids)
    ]
    plan = {
        "task_order": task_order,
        "policy": {
            "slot": "synthetic-slot",
            "policy_manifest_digest": "sha256:" + "a" * 64,
            "policy_manifest": {
                "policy_id": "synthetic-policy",
                "model": "synthetic-model",
                "provider": "synthetic-provider",
                "coordinate_adapter": "identity",
                "memory_policy_version": "synthetic-memory-v1",
                "parser_version": "synthetic-parser-v1",
                "response_schema_version": "synthetic-response-v1",
                "max_model_attempts_per_action": 1,
                "transport_retry_rule": "none",
            },
        },
    }
    summary = {
        "episode_results": [
            {
                "trial_id": trial_id,
                "task_id": task_id,
                "classification": classification,
                "success": False,
                "environment_actions": 0,
                "model_attempts": 1,
                "provider_wire_requests": 1,
            }
            for task_id, trial_id, classification in zip(
                task_ids, trial_ids, classifications, strict=True
            )
        ],
        "transport_records": [
            {
                "idempotency_key": idempotency_key,
                "status": (
                    "response"
                    if classification == "invalid_output"
                    else classification
                ),
                "latency_ms": 1.0,
                "cost_usd": "0.00",
            }
            for idempotency_key, classification in zip(
                idempotency_keys, classifications, strict=True
            )
        ],
        "approved_plan_sha256": "sha256:" + "b" * 64,
        "journal_integrity": {"event_chain_digest": "sha256:" + "c" * 64},
        "code_revision": "synthetic-revision",
        "provider_control_requests": 0,
        "phase_spend": {
            "schema_version": "pixelgym-agent-v5-d56-phase-spend-v2",
            "known_spend_usd": "0",
            "unknown_reservation_usd": "0",
            "in_flight_reservation_usd": "0",
            "budget_accounted_spend_usd": "0",
        },
        "campaign_spend": {
            "schema_version": "pixelgym-agent-v5-d56-phase-spend-v2",
            "known_spend_usd": "0",
            "unknown_reservation_usd": "0",
            "in_flight_reservation_usd": "0",
            "budget_accounted_spend_usd": "0",
        },
        "calibration_incremental_spend_usd": "0",
        "known_prior_aggregate_spend_usd": "0",
        "unknown_prior_charge_reservation_usd": "0",
        "prior_aggregate_spend_usd": "0",
        "actual_aggregate_spend_usd": "0",
        "budget_accounted_aggregate_spend_usd": "0",
        "remaining_aggregate_spend_usd": "1",
        "maximum_aggregate_spend_usd": "1",
    }
    manifest = {
        "manifest_digest": "sha256:" + "d" * 64,
        "records": [
            {
                "task_id": task_id,
                "seed_record": {
                    "family": "synthetic_family",
                    "difficulty_band": "synthetic_band",
                    "logical_id": f"logical-{index}",
                    "variant": "base",
                },
            }
            for index, task_id in enumerate(task_ids)
        ],
    }
    derivative = build_derivative(
        plan=plan,
        summary=summary,
        manifest=manifest,
        audit={
            "result": {"checks_failed": 0},
            "artifacts": {
                "run_summary": {"sha256": "sha256:" + "e" * 64},
                "attempt_journal": {"sha256": "sha256:" + "f" * 64},
            },
        },
        projection=_journal_projection(journal_path),
        audit_file_sha256="sha256:" + "1" * 64,
    )

    assert derivative["coverage"]["attempted_tasks"] == 3
    assert derivative["coverage"]["invalid_outputs"] == 1
    assert derivative["coverage"]["infrastructure_failures"] == 1
    assert derivative["coverage"]["policy_violations"] == 1
    family = derivative["families"][0]
    assert family["invalid_outputs"] == 1
    assert family["infrastructure_failures"] == 1
    assert family["policy_violations"] == 1
    report = render_report(derivative, derivative_sha256="sha256:" + "2" * 64)
    assert "| Infrastructure failures | 1 |" in report
    assert "| Policy violations | 1 |" in report
