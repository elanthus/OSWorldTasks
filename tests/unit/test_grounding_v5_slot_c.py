"""Slot C route identity and bounded, disjoint smoke/calibration allocations."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL_RETRY_SUCCESSOR,
    LLAMA_STATEFUL_VERTEX,
    LLAMA_STATEFUL_VERTEX_SMOKE,
    OpenRouterPanelPolicy,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.provider_adapters import OpenRouterHttpAdapter
from pixelgym.grounding.v5.slot_c import build_slot_c_plan
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]


def test_vertex_keeps_policy_contract_except_route_prices_and_identity():
    assert LLAMA_STATEFUL_VERTEX == replace(
        LLAMA_STATEFUL_RETRY_SUCCESSOR,
        slot="C-llama-stateful-vertex-v1",
        provider_route="google-vertex/us-east5",
        response_provider="Google",
        quantizations=(),
        prompt_price_per_token_usd=Decimal("0.00000025"),
        completion_price_per_token_usd=Decimal("0.0000007"),
    )
    old, new = [
        build_panel_policy_manifest(ROOT, config=config, code_revision="a" * 40)
        for config in (LLAMA_STATEFUL_RETRY_SUCCESSOR, LLAMA_STATEFUL_VERTEX)
    ]
    for field in (
        "system_prompt_digest",
        "memory_policy_version",
        "state_reducer_version",
        "parser_version",
        "request_deadline_seconds",
        "model",
    ):
        assert getattr(new, field) == getattr(old, field)
    assert new.policy_id != old.policy_id
    assert LLAMA_STATEFUL_RETRY_SUCCESSOR.quantizations == ("fp8",)


def test_vertex_request_pins_route_and_strict_schema_without_claiming_quantization():
    policy = OpenRouterPanelPolicy(LLAMA_STATEFUL_VERTEX_SMOKE)
    request = policy.build_request(policy.reset("instruction"), bytes(1024 * 768 * 3))
    assert request["provider"] == {
        "only": ["google-vertex/us-east5"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert request["model"] == "meta-llama/llama-4-scout"
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize("provider", ["Google", "DeepInfra"])
def test_vertex_parser_accepts_only_the_selected_response_provider(provider):
    policy = OpenRouterPanelPolicy(LLAMA_STATEFUL_VERTEX_SMOKE)
    state = policy.reset("instruction")
    response = canonical_json_bytes(
        {
            "response_id": "fixture-response",
            "model": "meta-llama/llama-4-scout",
            "content": '{"action_type":0,"x":0,"y":0,"key":0}',
            "finish_reason": "stop",
            "usage": {"upstream_provider": provider, "price_guard": "ok", "cost": "0.0001"},
        }
    )
    if provider == "Google":
        assert policy.parse(response, state)["action_type"] == 0
    else:
        with pytest.raises(ValueError, match="provider"):
            policy.parse(response, state)


def test_vertex_smoke_has_twenty_calls_without_retries_and_full_run_keeps_fifty_tasks():
    smoke, calibration = [
        build_slot_c_plan(
            ROOT,
            code_revision="a" * 40,
            phase=phase,
            maximum_spend_usd="1" if phase == "smoke" else "5",
            output_directory=f"artifacts/slot-c-test-unused-{phase}",
        )
        for phase in ("smoke", "calibration")
    ]
    assert len(smoke.assignments) == 10
    assert len({a.family for a in smoke.assignments}) == 6
    assert {a.action_limit for a in smoke.assignments} == {2}
    assert smoke.budgets.caps.model_attempt_cap == 20
    assert smoke.budgets.caps.provider_wire_request_cap == 20
    assert smoke.retry_breaker.max_bounded_retries_per_action == 0
    assert smoke.policies[0].policy_manifest["max_model_attempts_per_action"] == 1
    assert len(calibration.assignments) == 50
    assert calibration.budgets.caps.environment_action_cap == 1431
    assert calibration.budgets.caps.model_attempt_cap == 5724
    assert calibration.budgets.caps.provider_wire_request_cap == 5724
    assert calibration.retry_breaker.max_bounded_retries_per_action == 3
    assert not {a.seed for a in smoke.assignments} & {a.seed for a in calibration.assignments}
    source = json.loads((ROOT / calibration.manifest_path).read_text())
    assert [(a.seed, a.task_id, a.action_limit) for a in calibration.assignments] == [
        (r["seed_record"]["seed"], r["task_id"], r["max_episode_steps"]) for r in source["records"]
    ]
    for plan in (smoke, calibration):
        assert plan.budgets.caps.provider_control_request_cap == 0
        assert plan.outputs.resume_mode == "forbid"
        assert plan.raw["approval_required"]["owner"] == "human"
        adapter = OpenRouterHttpAdapter(ROOT, plan)
        try:
            assert adapter._manifest(plan.policies[0].slot).to_dict() == (
                plan.policies[0].policy_manifest
            )
            assert adapter.provider_accounting()["provider_calls_made"] == 0
        finally:
            adapter.close()


def test_slot_c_rejects_confirmatory_phase():
    with pytest.raises(ValueError, match="only smoke and calibration"):
        build_slot_c_plan(
            ROOT,
            code_revision="a" * 40,
            phase="confirmatory",
            maximum_spend_usd="5",
            output_directory="artifacts/slot-c-test-unused",
        )
