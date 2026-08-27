from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.panel_smoke import build_plan, plan_digest

ROOT = Path(__file__).parents[2]


def test_panel_smoke_plan_is_no_call_development_only_and_inside_cap() -> None:
    plan = build_plan(ROOT)

    assert plan["provider_calls_made"] == 0
    assert len(plan["policies"]) == 4
    assert [record["slot"] for record in plan["policies"]] == [
        "A-gemini-stateful",
        "B-qwen-stateful",
        "C-llama-stateful",
        "D-qwen-stateless",
    ]
    assert {record["task"]["partition"] for record in plan["policies"]} == {
        "development"
    }
    assert len({record["task"]["task_id"] for record in plan["policies"]}) == 4
    assert [record["task"]["seed"] for record in plan["policies"]] == [
        5002,
        5006,
        5010,
        5018,
    ]
    assert plan["caps"]["environment_action_cap"] == 4
    assert plan["caps"]["model_attempt_cap"] == 8
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert plan["caps"]["provider_wire_request_cap"] == 8
    assert Decimal(plan["caps"]["aggregate_theoretical_upper_bound_usd"]) < Decimal(10)
    assert plan_digest(plan).startswith("sha256:")


def test_panel_smoke_price_records_fail_closed_and_bind_routes() -> None:
    plan = build_plan(ROOT)
    records = {record["slot"]: record for record in plan["policies"]}

    assert records["A-gemini-stateful"]["provider"]["only"] == [
        "google-vertex/global"
    ]
    assert records["A-gemini-stateful"]["provider"]["upstream_provider"] == "Google"
    assert records["A-gemini-stateful"]["price_record"][
        "per_request_theoretical_maximum_usd"
    ] == "0.055296000"
    assert records["B-qwen-stateful"]["provider"]["only"] == ["alibaba"]
    assert records["C-llama-stateful"]["provider"]["only"] == ["deepinfra"]
    assert records["C-llama-stateful"]["provider"]["quantizations"] == ["fp8"]
    assert records["D-qwen-stateless"]["provider"]["only"] == ["alibaba"]
    assert {
        record["price_record"]["unknown_usage_or_price_rule"]
        for record in plan["policies"]
    } == {"fail_closed"}
