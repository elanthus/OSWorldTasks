"""No-call, non-executable planning for the approved five-dollar repair budget."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.grounding.v5.panel_policy import (
    GEMINI_STATEFUL_FULL_CALIBRATION,
    MAX_OUTPUT_TOKENS,
    PanelPolicyConfig,
)
from pixelgym.grounding.v5.policies import _append_golden_stage
from pixelgym.grounding.v5.screenshot_memory import build_screenshot_policy_manifest

TOTAL_REPAIR_BUDGET_USD = Decimal("5.00")
# These are development diagnostics with a declared scripted prefix, not
# end-to-end episodes or confirmatory observations. Five consumers of each kind.
PILOT_SEEDS = (5000, 5004, 5008, 5012, 5016, 5020, 5001, 5005, 5009, 5013)


@dataclass(frozen=True)
class ScreenshotPriceConfig(PanelPolicyConfig):
    upstream_context_length: int = 1_048_576

    @property
    def request_maximum_usd(self) -> Decimal:
        # Do not assume that the legacy harness's smaller context declaration
        # constrains this endpoint. Reserve the full upstream input bound PLUS
        # the output cap, conservatively ignoring combined-context overlap.
        return (
            self.prompt_price_per_token_usd * self.upstream_context_length
            + self.completion_price_per_token_usd * MAX_OUTPUT_TOKENS
        )


def config_from_price_snapshot(snapshot: dict[str, Any]) -> ScreenshotPriceConfig:
    endpoints = snapshot["endpoints"]
    if not endpoints or any(not row["tag"].startswith("google-vertex/global") for row in endpoints):
        raise ValueError("price snapshot must cover the declared Vertex route")
    if not any(row["tag"] == "google-vertex/global" for row in endpoints):
        raise ValueError("the approved base route is missing from the price snapshot")
    rates = [Decimal(row["pricing"][key]) for row in endpoints for key in ("prompt", "completion")]
    if any(not rate.is_finite() or rate <= 0 for rate in rates):
        raise ValueError("endpoint prices must be finite positive amounts")
    contexts = [row["context_length"] for row in endpoints]
    if any(type(value) is not int or value <= 0 for value in contexts):
        raise ValueError("endpoint context bounds must be positive integers")
    base = replace(
        GEMINI_STATEFUL_FULL_CALIBRATION,
        slot="d58-gemini-screenshot-candidate",
        stateful=False,
        controlled_history_prompt=True,
        prompt_price_per_token_usd=max(Decimal(row["pricing"]["prompt"]) for row in endpoints),
        completion_price_per_token_usd=max(
            Decimal(row["pricing"]["completion"]) for row in endpoints
        ),
        max_model_attempts_per_action=1,
        max_rate_limit_retries_per_action=0,
        max_bounded_retries_per_action=0,
        enforce_provider_price_cap=True,
    )
    return ScreenshotPriceConfig(**vars(base), upstream_context_length=max(contexts))


def pilot_plan(root: Path, *, snapshot: dict[str, Any], code_revision: str) -> dict[str, Any]:
    config = config_from_price_snapshot(snapshot)
    cases: list[dict[str, Any]] = []
    for index, seed in enumerate(PILOT_SEEDS):
        task = generate_memory_task(seed)
        consumer = 5 if index % 2 == 0 else 7
        backend = MemoryBackend()
        prefix_actions: list[dict[str, int]] = []
        try:
            backend.reset(seed)
            for stage in task.stages[:consumer]:
                _append_golden_stage(backend, stage, prefix_actions)
        finally:
            backend.close()
        cases.append(
            {
                "seed": seed,
                "task_id": task.task_id,
                "task_digest": content_digest(task.canonical_dict()),
                "consumer_index": consumer,
                "scripted_prefix_action_count": len(prefix_actions),
                "scripted_prefix_actions_digest": content_digest(prefix_actions),
                "prefix": "scripted_golden_actions_only; retained as diagnostic context",
            }
        )
    manifests = {}
    for retain in (True, False):
        manifest = build_screenshot_policy_manifest(
            root, config=config, code_revision=code_revision, retain_screenshots=retain
        )
        # Bind the actual upstream bound used in the conservative price reservation.
        fields = {key: value for key, value in vars(manifest).items() if key != "policy_id"}
        fields["context_limit"] = config.upstream_context_length
        manifest = type(manifest).build(**fields)
        manifests["history" if retain else "stateless"] = manifest.to_dict()
    requests = 2 * len(cases)
    prefix_actions_count = 2 * sum(case["scripted_prefix_action_count"] for case in cases)
    cap = CallCaps(prefix_actions_count + requests, requests, 0, requests)
    value = {
        "schema_version": "pixelgym-v5-d58-memory-diagnostic-plan-v1",
        "status": "plan_only_requires_exact_execution_approval",
        "execution_enabled": False,
        "purpose": "scripted-prefix first-attempt memory diagnostic; not an end-to-end calibration score",
        "cases": cases,
        "policy_manifests": manifests,
        "price_snapshot_digest": content_digest(snapshot),
        "caps": cap.to_dict(),
        "scripted_prefix_actions_count_as_model_calls": False,
        "scripted_environment_action_cap": prefix_actions_count,
        "model_selected_environment_action_cap": requests,
        "spend": {
            "aggregate_successor_ceiling_usd": str(TOTAL_REPAIR_BUDGET_USD),
            "known_additional_spend_usd": "0",
            "per_request_reservation_usd": str(config.request_maximum_usd),
            "uncapped_twenty_request_bound_usd": str(requests * config.request_maximum_usd),
            "all_requests_guaranteed_to_fit": requests * config.request_maximum_usd
            <= TOTAL_REPAIR_BUDGET_USD,
            "ledger_scope": "one durable ledger shared across both arms and every subsequent D5.8 phase",
            "stop_rule": "reserve full request bound before send; stop before a reservation exceeds USD 5; retain unknown charges at the full bound; never start a new per-arm or per-phase allowance",
        },
        "confirmatory_request_cap": 0,
        "provider_calls_made": 0,
    }
    return {**value, "plan_digest": content_digest(value)}
