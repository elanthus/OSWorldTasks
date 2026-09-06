from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.d56_calibration import (
    CONSECUTIVE_FAILURE_LIMIT,
    CURRENT_CALIBRATION_MANIFEST,
    HISTORICAL_CALIBRATION_MANIFEST,
    ConsecutiveFailureBreaker,
    _current_calibration_manifest,
    _historical_calibration_manifest,
    build_plan,
    execute_calibration,
    plan_digest,
)
from pixelgym.grounding.v5.journal import V5AttemptJournal

ROOT = Path(__file__).parents[2]


def fake_smoke_output(tmp_path: Path) -> Path:
    output = tmp_path / "smoke"
    output.mkdir()
    journal = V5AttemptJournal(output / "attempts.sqlite")
    try:
        for index in range(4):
            _event, created = journal.reserve_attempt_started(
                AttemptIdentity(f"smoke-{index}", 0, 0),
                provider_endpoint_identity="openrouter",
                request_digest=f"sha256:request-{index}",
                idempotency_key=f"smoke-{index}",
                model_attempt_reservation=1,
                control_request_reservation=0,
                pre_call_checkpoint=b"checkpoint",
                approved_caps=CallCaps(4, 4, 0, 4),
            )
            assert created
        journal_integrity = journal.integrity_report()
    finally:
        journal.close()
    (output / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "pixelgym-agent-v5-panel-smoke-result-v2",
                "approved_plan_sha256": "sha256:smoke-plan",
                "provider_calls_made": 4,
                "provider_wire_requests": 4,
                "model_attempt_reservations": 4,
                "provider_control_requests": 0,
                "actual_aggregate_spend_usd": "0.38",
                "episode_results": [
                    {"slot": slot, "classification": "pilot_action_limit"}
                    for slot in (
                        "A-gemini-stateful",
                        "B-qwen-stateful",
                        "C-llama-stateful",
                        "D-qwen-stateless",
                    )
                ],
                "journal_integrity": journal_integrity,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def test_d56_plan_binds_four_policies_fifty_clean_tasks_and_per_run_cap(
    tmp_path: Path,
) -> None:
    plan = build_plan(ROOT, smoke_output_directory=fake_smoke_output(tmp_path))

    assert plan["provider_calls_made"] == 0
    assert plan["calibration_partition"]["episode_count"] == 50
    assert plan["calibration_partition"]["action_cap_per_policy"] == 1431
    assert len(plan["calibration_partition"]["excluded_seeds"]) == 18
    assert len(plan["task_order"]) == 50
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert len(plan["policies"]) == 4
    assert [record["slot"] for record in plan["policies"]] == [
        "A-gemini-stateful",
        "B-qwen-stateful",
        "C-llama-stateful",
        "D-qwen-stateless",
    ]
    assert all(record["caps"]["model_attempt_cap"] == 2862 for record in plan["policies"])
    assert all(
        set(record["phase_call_cap_plan"]["phases"])
        == {
            "calibration",
            "confirmatory_primary",
            "stateless_ablation",
            "reliability_repeats",
        }
        for record in plan["policies"]
    )
    assert plan["aggregate_caps"]["environment_action_cap"] == 5724
    assert plan["aggregate_caps"]["model_attempt_cap"] == 11448
    assert plan["aggregate_caps"]["provider_control_request_cap"] == 0
    assert plan["aggregate_caps"]["provider_wire_request_cap"] == 11448
    assert plan["aggregate_caps"]["maximum_run_spend_usd"] == "10.00"
    assert plan["aggregate_caps"]["remaining_run_spend_usd"] == "10.00"
    assert plan["aggregate_caps"]["prior_campaign_spend"] == {
        "schema_version": "pixelgym-agent-v5-d56-phase-spend-v1",
        "known_spend_usd": "0.38",
        "unknown_reservation_usd": "unknown",
        "in_flight_reservation_usd": "unknown",
        "budget_accounted_spend_usd": "unknown",
    }
    assert plan_digest(plan).startswith("sha256:")


def test_d56_current_plans_and_historical_validators_use_separate_manifests() -> None:
    current = _current_calibration_manifest(ROOT)
    historical = _historical_calibration_manifest(ROOT)

    assert CURRENT_CALIBRATION_MANIFEST.as_posix().endswith(
        "grounding-v5-manifests/v2/calibration-d56.json"
    )
    assert HISTORICAL_CALIBRATION_MANIFEST.as_posix().endswith(
        "grounding-v5-manifests/calibration-d56.json"
    )
    assert current["manifest_digest"] != historical["manifest_digest"]
    assert current["records"] == historical["records"]


def test_d56_plan_discloses_uncapped_maximum_but_enforces_per_run_guard(
    tmp_path: Path,
) -> None:
    plan = build_plan(ROOT, smoke_output_directory=fake_smoke_output(tmp_path))

    assert Decimal(
        plan["aggregate_caps"]["uncapped_theoretical_request_maximum_usd"]
    ) > Decimal(10)
    assert "this phase's ledger" in plan["aggregate_caps"]["enforcement"]
    assert all(
        record["price_record"]["unknown_usage_or_price_rule"] == "fail_closed"
        for record in plan["policies"]
    )


def test_consumed_native_d56_execution_is_locked_after_adapter_replacement(
    tmp_path: Path,
) -> None:
    smoke_output = fake_smoke_output(tmp_path)
    plan = build_plan(ROOT, smoke_output_directory=smoke_output)
    output = tmp_path / "must-not-exist"

    with pytest.raises(RuntimeError, match="native-coordinate D5.6 calibration is frozen"):
        execute_calibration(
            ROOT,
            plan=plan,
            approved_plan_sha256=plan_digest(plan),
            smoke_output_directory=smoke_output,
            output_directory=output,
        )

    assert not output.exists()


def test_breaker_keeps_running_after_an_isolated_malformed_output() -> None:
    breaker = ConsecutiveFailureBreaker()

    # This is the exact shape that stopped the frozen Qwen run at task 13 of 50.
    assert breaker.record("step_limit_truncation") is False
    assert breaker.record("invalid_output") is False
    assert breaker.record("step_limit_truncation") is False
    assert breaker.tripped is False
    assert breaker.longest_failure_streak == 1


def test_breaker_stops_a_sustained_failure_streak() -> None:
    breaker = ConsecutiveFailureBreaker(limit=3)

    assert breaker.record("invalid_output") is False
    assert breaker.record("infrastructure_failure") is False
    assert breaker.record("request_failure") is True
    assert breaker.tripped is True
    assert breaker.trip_reason == "consecutive_failure_limit_reached"


def test_breaker_streak_resets_on_a_normal_classification() -> None:
    breaker = ConsecutiveFailureBreaker(limit=3)

    for _ in range(2):
        assert breaker.record("invalid_output") is False
    assert breaker.record("success_termination") is False
    for _ in range(2):
        assert breaker.record("invalid_output") is False
    assert breaker.tripped is False
    assert breaker.to_dict()["longest_consecutive_failure_streak"] == 2


def test_breaker_records_an_external_stop_reason() -> None:
    breaker = ConsecutiveFailureBreaker()
    breaker.trip("aggregate_spend_ledger_blocked")

    assert breaker.to_dict() == {
        "consecutive_failure_limit": CONSECUTIVE_FAILURE_LIMIT,
        "longest_consecutive_failure_streak": 0,
        "tripped": True,
        "trip_reason": "aggregate_spend_ledger_blocked",
    }


def test_breaker_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="consecutive failure limit"):
        ConsecutiveFailureBreaker(limit=0)
