"""The Llama retry successor changes retries only and requires its own plan."""

from dataclasses import replace
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.controlled_comparison import build_comparison_plan
from pixelgym.grounding.v5.panel_policy import (
    LLAMA_STATEFUL,
    LLAMA_STATEFUL_RETRY_SUCCESSOR,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.provider_adapters import OpenRouterHttpAdapter

ROOT = Path(__file__).resolve().parents[2]


def test_successor_preserves_route_prompt_and_deadline():
    assert LLAMA_STATEFUL_RETRY_SUCCESSOR == replace(
        LLAMA_STATEFUL,
        slot="C-llama-stateful-v2",
        max_model_attempts_per_action=4,
        max_rate_limit_retries_per_action=3,
        max_bounded_retries_per_action=3,
        rate_limit_backoff_base_seconds=15.0,
    )
    assert LLAMA_STATEFUL.max_model_attempts_per_action == 2
    assert LLAMA_STATEFUL.rate_limit_backoff_base_seconds == 2.0
    old = build_panel_policy_manifest(ROOT, config=LLAMA_STATEFUL, code_revision="a" * 40)
    new = build_panel_policy_manifest(
        ROOT, config=LLAMA_STATEFUL_RETRY_SUCCESSOR, code_revision="a" * 40
    )
    assert old.system_prompt_digest == new.system_prompt_digest
    assert old.request_deadline_seconds == new.request_deadline_seconds == 180.0
    assert old.provider == new.provider == "openrouter/deepinfra"
    assert old.model == new.model == "meta-llama/llama-4-scout"


def test_successor_is_registered_and_validated_without_provider_calls():
    raw = build_comparison_plan(
        ROOT,
        code_revision="a" * 40,
        phase="calibration",
        maximum_spend_usd="2",
        output_directory="artifacts/llama-successor-test-unused",
    ).raw
    config = LLAMA_STATEFUL_RETRY_SUCCESSOR
    manifest = build_panel_policy_manifest(ROOT, config=config, code_revision="a" * 40).to_dict()
    raw["policy_panel"] = [
        {
            "slot": config.slot,
            "policy_manifest": manifest,
            "policy_manifest_digest": content_digest(manifest),
        }
    ]
    assignments = raw["task_allocation"]["assignments"]
    selected = [a for a in assignments if a["slot"] == assignments[0]["slot"]]
    raw["task_allocation"]["assignments"] = [
        dict(a, ordinal=i, slot=config.slot) for i, a in enumerate(selected)
    ]
    raw["budgets"].update(
        environment_action_cap=1431,
        model_attempt_cap=5724,
        provider_wire_request_cap=5724,
        per_request_theoretical_maximum_usd=str(config.request_maximum_usd),
    )
    plan = CalibrationPlan.from_dict(raw)
    assert len(plan.assignments) == 50
    assert plan.budgets.caps.model_attempt_cap == 5724
    OpenRouterHttpAdapter(ROOT, plan).close()
