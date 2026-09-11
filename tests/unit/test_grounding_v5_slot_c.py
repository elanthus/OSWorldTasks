"""Slot C route identity and bounded development-only diagnostic allocations."""

import io
import urllib.error
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL_RETRY_SUCCESSOR,
    LLAMA_STATEFUL_VERTEX,
    LLAMA_STATEFUL_VERTEX_DIAGNOSTIC,
    LLAMA_STATEFUL_VERTEX_SMOKE,
    MISTRAL_STATEFUL_SMOKE,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
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
    assert "reasoning" not in request


def test_mistral_smoke_binds_reasoning_route_budget_and_registered_manifest():
    config = MISTRAL_STATEFUL_SMOKE
    policy = OpenRouterPanelPolicy(config)
    request = policy.build_request(policy.reset("instruction"), bytes(1024 * 768 * 3))
    assert request["model"] == "mistralai/mistral-small-2603"
    assert request["provider"] == {
        "only": ["mistral"], "allow_fallbacks": False,
        "data_collection": "deny", "require_parameters": True,
        "max_price": {"prompt": 0.15, "completion": 0.6},
    }
    assert request["reasoning"] == {"effort": "none"}
    assert request["response_format"]["json_schema"]["strict"] is True
    assert config.router_metadata
    assert config.request_maximum_usd == Decimal("0.02150400")
    plan = build_slot_c_plan(
        ROOT, code_revision="a" * 40, phase="smoke", candidate="mistral",
        maximum_spend_usd="1.00", output_directory="artifacts/mistral-test-unused",
    )
    assert len(plan.assignments) == 10
    assert len({a.family for a in plan.assignments}) == 6
    assert {a.action_limit for a in plan.assignments} == {2}
    assert plan.budgets.caps.model_attempt_cap == 20
    assert plan.budgets.caps.provider_wire_request_cap == 20
    assert plan.budgets.caps.provider_control_request_cap == 0
    assert plan.retry_breaker.max_bounded_retries_per_action == 0
    assert plan.outputs.resume_mode == "forbid"
    assert {a.slot for a in plan.assignments} == {config.slot}
    manifest = build_panel_policy_manifest(ROOT, config=config, code_revision="a" * 40)
    assert ("reasoning_effort", "none") in manifest.inference_parameters
    changed = build_panel_policy_manifest(
        ROOT, config=replace(config, reasoning_effort="high"), code_revision="a" * 40,
    )
    assert manifest.policy_id != changed.policy_id
    uncapped = build_panel_policy_manifest(
        ROOT, config=replace(config, enforce_provider_price_cap=False), code_revision="a" * 40,
    )
    assert manifest.policy_id != uncapped.policy_id
    adapter = OpenRouterHttpAdapter(ROOT, plan)
    try:
        assert adapter._manifest(config.slot).to_dict() == manifest.to_dict()
        assert adapter.provider_accounting()["provider_calls_made"] == 0
    finally:
        adapter.close()


@pytest.mark.parametrize("model,provider,accepted", [
    ("mistralai/mistral-small-2603", "Mistral", True),
    ("mistralai/mistral-small-2603", "Venice", False),
    ("meta-llama/llama-4-scout", "Mistral", False),
])
def test_mistral_parser_rejects_other_model_or_provider(model, provider, accepted):
    policy = OpenRouterPanelPolicy(MISTRAL_STATEFUL_SMOKE)
    response = canonical_json_bytes({
        "response_id": "fixture", "model": model,
        "content": '{"action_type":0,"x":0,"y":0,"key":0}',
        "finish_reason": "stop",
        "usage": {"upstream_provider": provider, "price_guard": "ok", "cost": "0.0001"},
    })
    if accepted:
        assert policy.parse(response, policy.reset("instruction"))["action_type"] == 0
    else:
        with pytest.raises(ValueError, match="provider response"):
            policy.parse(response, policy.reset("instruction"))


@pytest.mark.parametrize("candidate,phase", [
    ("mistral", "diagnostic"),
    ("mistral", "confirmatory"), ("unknown", "smoke"),
])
def test_slot_c_rejects_unprepared_candidate_phases(candidate, phase):
    with pytest.raises(ValueError):
        build_slot_c_plan(
            ROOT, code_revision="a" * 40, phase=phase, candidate=candidate,
            maximum_spend_usd="1", output_directory="artifacts/unprepared-test-unused",
        )


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


def test_vertex_development_plans_bound_calls_and_register_exact_identities():
    smoke, diagnostic = [
        build_slot_c_plan(
            ROOT,
            code_revision="a" * 40,
            phase=phase,
            maximum_spend_usd="1" if phase == "smoke" else "0.05",
            output_directory=f"artifacts/slot-c-test-unused-{phase}",
        )
        for phase in ("smoke", "diagnostic")
    ]
    assert len(smoke.assignments) == 10
    assert len({a.family for a in smoke.assignments}) == 6
    assert {a.action_limit for a in smoke.assignments} == {2}
    assert smoke.budgets.caps.model_attempt_cap == 20
    assert smoke.budgets.caps.provider_wire_request_cap == 20
    assert smoke.retry_breaker.max_bounded_retries_per_action == 0
    assert smoke.policies[0].policy_manifest["max_model_attempts_per_action"] == 1
    assert len(diagnostic.assignments) == 1
    assert diagnostic.assignments[0].seed == smoke.assignments[0].seed
    assert diagnostic.budgets.caps.environment_action_cap == 1
    assert diagnostic.budgets.caps.model_attempt_cap == 1
    assert diagnostic.budgets.caps.provider_wire_request_cap == 1
    assert diagnostic.retry_breaker.max_bounded_retries_per_action == 0
    assert LLAMA_STATEFUL_VERTEX_DIAGNOSTIC == replace(
        LLAMA_STATEFUL_VERTEX_SMOKE,
        slot="C-llama-stateful-vertex-v1-routing-diagnostic",
        router_metadata=True,
    )
    assert LLAMA_STATEFUL_VERTEX_SMOKE.router_metadata is False
    for plan in (smoke, diagnostic):
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


@pytest.mark.parametrize("phase", ["calibration", "confirmatory"])
def test_slot_c_rejects_later_phases_without_successful_smoke_review(phase):
    with pytest.raises(ValueError, match="calibration is blocked"):
        build_slot_c_plan(
            ROOT,
            code_revision="a" * 40,
            phase=phase,
            maximum_spend_usd="5",
            output_directory="artifacts/slot-c-test-unused",
        )


def test_vertex_http_failure_keeps_block_and_reservation_after_journal_reopen(tmp_path):
    sent = []

    def urlopen(request, **kwargs):
        sent.append(request)
        assert request.headers["X-openrouter-metadata"] == "enabled"
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", None,
            io.BytesIO(b'{"error":{"code":404,"message":"private"}}'),
        )

    path = tmp_path / "vertex-http-failure.sqlite"
    journal = V5AttemptJournal(path)
    ledger = SpendLedger(Decimal("0.05"), Decimal(0), journal=journal)
    policy = OpenRouterPanelPolicy(LLAMA_STATEFUL_VERTEX_DIAGNOSTIC)
    request = policy.build_request(policy.reset("instruction"), bytes(1024 * 768 * 3))
    transport = OpenRouterPanelTransport(
        LLAMA_STATEFUL_VERTEX_DIAGNOSTIC, ledger=ledger,
        environment={"OPENROUTER_API_KEY": "fixture-key"}, urlopen=urlopen,
    )
    result = transport.send(request, idempotency_key="first", deadline_seconds=1)
    assert result.status == "unknown"
    assert ledger.blocked
    assert ledger.unknown_reservation_usd == LLAMA_STATEFUL_VERTEX_DIAGNOSTIC.request_maximum_usd
    before = ledger.to_dict()
    ledger.block()  # A repeated stop must be idempotent.
    assert sum(e.kind == "spend_ledger_blocked" for e in journal.events()) == 1
    journal.close()

    replay = V5AttemptJournal(path)
    try:
        resumed = SpendLedger(Decimal("0.05"), Decimal(0), journal=replay)
        assert resumed.to_dict() == before
        assert not resumed.reserve_wire("second", Decimal("0.001"))
        assert len(sent) == 1
        assert "private" not in str([e.payload for e in replay.events()])
    finally:
        replay.close()


@pytest.mark.parametrize("failure", ["conflicting_charge", "released_charge", "hold_mismatch"])
def test_spend_accounting_violation_remains_blocked_after_reopen(tmp_path, failure):
    path = tmp_path / "accounting-violation.sqlite"
    journal = V5AttemptJournal(path)
    ledger = SpendLedger(Decimal(1), Decimal(0), journal=journal)
    assert ledger.reserve_wire("request", Decimal("0.1"))
    if failure == "conflicting_charge":
        assert ledger.record_cost("request", Decimal("0.01"), Decimal("0.1"))
        assert not ledger.record_cost("request", Decimal("0.02"), Decimal("0.1"))
    elif failure == "released_charge":
        assert ledger.release_wire("request", reason="fixture-proven-zero-charge")
        assert not ledger.record_cost("request", Decimal("0.01"), Decimal("0.1"))
    else:
        ledger.reserve_unknown_charge("request", Decimal("0.2"))
    assert ledger.blocked
    before = ledger.to_dict()
    journal.close()
    replay = V5AttemptJournal(path)
    try:
        resumed = SpendLedger(Decimal(1), Decimal(0), journal=replay)
        assert resumed.to_dict() == before
        assert not resumed.reserve_wire("next", Decimal("0.1"))
    finally:
        replay.close()
