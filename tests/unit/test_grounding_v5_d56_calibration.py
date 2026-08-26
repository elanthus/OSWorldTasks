from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import AttemptIdentity, CallCaps
from pixelgym.grounding.v5.d56_calibration import build_plan, plan_digest
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
                "schema_version": "pixelgym-agent-v5-panel-smoke-result-v1",
                "approved_plan_sha256": "sha256:smoke-plan",
                "provider_calls_made": 4,
                "provider_wire_requests": 4,
                "model_attempt_reservations": 4,
                "provider_control_requests": 0,
                "actual_aggregate_spend_usd": "0.01",
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


def test_d56_plan_binds_four_policies_fifty_clean_tasks_and_shared_cap(
    tmp_path: Path,
) -> None:
    plan = build_plan(ROOT, smoke_output_directory=fake_smoke_output(tmp_path))

    assert plan["provider_calls_made"] == 0
    assert plan["calibration_partition"]["episode_count"] == 50
    assert plan["calibration_partition"]["action_cap_per_policy"] == 1431
    assert len(plan["calibration_partition"]["excluded_seeds"]) == 10
    assert len(plan["task_order"]) == 50
    assert len({record["task_id"] for record in plan["task_order"]}) == 50
    assert len(plan["policies"]) == 4
    assert [record["slot"] for record in plan["policies"]] == [
        "A-gemini-stateful",
        "B-qwen-stateful",
        "C-llama-stateful",
        "D-qwen-stateless",
    ]
    assert all(record["caps"]["model_attempt_cap"] == 1431 for record in plan["policies"])
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
    assert plan["aggregate_caps"]["model_attempt_cap"] == 5724
    assert plan["aggregate_caps"]["provider_control_request_cap"] == 0
    assert plan["aggregate_caps"]["provider_wire_request_cap"] == 5724
    assert plan["aggregate_caps"]["maximum_aggregate_spend_usd"] == "10.00"
    assert plan["aggregate_caps"]["prior_aggregate_spend_usd"] == "0.01"
    assert plan_digest(plan).startswith("sha256:")


def test_d56_plan_discloses_uncapped_maximum_but_enforces_ten_dollar_guard(
    tmp_path: Path,
) -> None:
    plan = build_plan(ROOT, smoke_output_directory=fake_smoke_output(tmp_path))

    assert Decimal(
        plan["aggregate_caps"]["uncapped_theoretical_request_maximum_usd"]
    ) > Decimal(10)
    assert "before each wire request" in plan["aggregate_caps"]["enforcement"]
    assert all(
        record["price_record"]["unknown_usage_or_price_rule"] == "fail_closed"
        for record in plan["policies"]
    )
