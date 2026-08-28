from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from scripts.generate_grounding_v5_d56_gemini_calibration_publication import (
    _known_cost,
    _latency_summary,
    render_report,
)


def test_known_cost_is_fixed_precision_and_ignores_unknown_costs() -> None:
    records = [
        {"cost_usd": "0.125"},
        {"cost_usd": None},
        {"cost_usd": "0.000000001"},
    ]

    assert _known_cost(records) == "0.125000001"
    assert _known_cost([]) == "0.000000000"


def test_latency_summary_discloses_missing_unknown_outcome() -> None:
    summary = _latency_summary(
        [
            {"latency_ms": 10.0},
            {"latency_ms": 20.0},
            {"latency_ms": None},
        ]
    )

    assert summary == {
        "method": "linear-interpolation-over-completed-response-latencies-v1",
        "request_count": 3,
        "measured_response_count": 2,
        "missing_count": 1,
        "sum_ms": 30.0,
        "mean_ms": 15.0,
        "median_ms": 15.0,
        "p95_ms": 19.5,
        "max_ms": 20.0,
    }


def test_report_is_rendered_only_from_publishable_derivative() -> None:
    derivative = {
        "coverage": {
            "assigned_tasks": 1,
            "attempted_tasks": 1,
            "successful_tasks": 0,
            "step_limit_truncations": 0,
            "infrastructure_failures": 1,
            "unattempted_tasks": 0,
            "completed_all_assigned_tasks": False,
        },
        "families": [
            {
                "family": "review_and_commit",
                "assigned_tasks": 1,
                "attempted_tasks": 1,
                "successful_tasks": 0,
                "step_limit_truncations": 0,
                "infrastructure_failures": 1,
                "unattempted_tasks": 0,
                "environment_actions": 20,
                "attempted_action_cap": 30,
                "known_cost_usd": "0.100000000",
                "latency": {"median_ms": 10.0, "p95_ms": 12.0},
            }
        ],
        "action_budget": {
            "assigned_environment_action_cap": 30,
            "attempted_environment_action_cap": 30,
            "committed_environment_actions": 20,
            "attempted_action_cap_utilization": 2 / 3,
        },
        "provider_requests": {
            "wire_requests": 21,
            "completed_responses": 20,
            "unknown_outcomes": 1,
            "latency": {
                "measured_response_count": 20,
                "sum_ms": 200.0,
                "mean_ms": 10.0,
                "median_ms": 10.0,
                "p95_ms": 12.0,
                "max_ms": 15.0,
            },
        },
        "cost": {
            "known_calibration_incremental_spend_usd": "0.100000000",
            "known_actual_aggregate_spend_usd": "0.200000000",
            "recorded_remaining_aggregate_spend_usd": "9.800000000",
        },
        "robustness": {
            "complete_pair_count": 0,
            "attempted_pair_count": 0,
            "concordant_attempted_pair_count": 0,
            "discordant_attempted_pair_count": 0,
            "unattempted_pair_count": 0,
            "pairs": [],
            "unpaired_twin_records": [],
        },
        "failure_routes": {
            "terminal_classification_counts": {"infrastructure_failure": 1},
            "committed_action_diagnostic_counts": {"correct_transition": 3},
            "unknown_provider_outcomes": [
                {
                    "task_id": "task-1",
                    "step_index": 20,
                    "journal_failure_code": "provider_request_unknown",
                    "transport_failure_code": "TimeoutError",
                }
            ],
        },
        "tasks": [
            {
                "ordinal": 0,
                "task_id": "task-1",
                "family": "review_and_commit",
                "difficulty_band": "ceiling_probe",
                "variant": "base",
                "outcome": "infrastructure_failure",
                "environment_actions": 20,
                "max_episode_steps": 30,
                "provider_wire_requests": 21,
                "completed_provider_responses": 20,
                "known_cost_usd": "0.100000000",
                "latency": {"median_ms": 10.0, "p95_ms": 12.0},
                "diagnostic_event_counts": {"correct_transition": 3},
            }
        ],
        "source_bindings": {
            "approved_plan_sha256": "sha256:plan",
            "restricted_attempt_journal_sha256": "sha256:journal",
            "journal_integrity": {"event_chain_digest": "sha256:chain"},
            "integrity_audit_sha256": "sha256:audit",
        },
        "limitations": ["Stored evidence is incomplete."],
    }

    report = render_report(derivative, derivative_sha256="sha256:derivative")

    assert "incomplete calibration evidence" in report
    assert "| `review_and_commit` | 1 | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| `infrastructure_failure` | 1 |" in report
    assert "$0.100000000" in report
    assert "10.0 / 12.0" in report
    assert "sha256:journal" in report
    assert Decimal(derivative["cost"]["known_actual_aggregate_spend_usd"]) == Decimal("0.200000000")


def test_checked_in_derivative_excludes_restricted_payload_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "artifacts/grounding-v5-d56-gemini-full-calibration-publishable.json"
    derivative = json.loads(path.read_text(encoding="utf-8"))

    keys: set[str] = set()

    def collect_keys(value: object) -> None:
        if isinstance(value, dict):
            keys.update(str(key) for key in value)
            for nested in value.values():
                collect_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_keys(nested)

    collect_keys(derivative)
    assert (
        not {
            "content",
            "idempotency_key",
            "provider_endpoint_identity",
            "request_digest",
            "response_id",
            "screenshot_digest",
        }
        & keys
    )
    encoded = json.dumps(derivative, sort_keys=True)
    assert "/Users/" not in encoded
    assert "data:image/" not in encoded


def test_publication_relation_marks_raw_journal_as_excluded() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "artifacts/grounding-v5-d56-gemini-full-calibration-publication-relation.json"
    relation = json.loads(path.read_text(encoding="utf-8"))

    assert relation["excluded_authoritative_artifacts"] == [
        {
            "git_status": "must_not_commit",
            "path": "artifacts/grounding-v5-d56-gemini-full-calibration-run/attempts.sqlite",
            "reason": "restricted provider responses, screenshots, and private checkpoints",
            "sha256": "sha256:99d6520bc24ab6dc2515d70d4977aed19602dfa6fa87f0e4102d61c583557c82",
            "size_bytes": 1_425_731_584,
        }
    ]
