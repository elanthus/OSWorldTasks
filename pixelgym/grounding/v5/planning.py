"""Free plan-only call-cap calculations for every v5 evaluation phase."""

from __future__ import annotations

from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, Partition, PolicyManifest
from pixelgym.grounding.v5.generator import tasks_for_partition


def call_cap_plan(manifest: PolicyManifest) -> dict[str, Any]:
    calibration = tasks_for_partition(Partition.CALIBRATION)
    confirmatory = tasks_for_partition(Partition.CONFIRMATORY)
    stateless_subset = confirmatory[:24]
    reliability_subset = confirmatory[:12]

    def calculate(tasks: tuple[Any, ...], *, repetitions: int = 1) -> dict[str, int]:
        steps = tuple(
            task.max_episode_steps for task in tasks for _ in range(repetitions)
        )
        return CallCaps.calculate(
            max_episode_steps=steps,
            max_model_attempts_per_action=manifest.max_model_attempts_per_action,
            max_cancellation_requests_per_attempt=(
                manifest.max_cancellation_requests_per_attempt
            ),
            max_reconciliation_requests_per_attempt=(
                manifest.max_reconciliation_requests_per_attempt
            ),
        ).to_dict()

    return {
        "schema_version": "pixelgym-agent-v5-call-cap-plan-v1",
        "policy_id": manifest.policy_id,
        "provider": manifest.provider,
        "model": manifest.model,
        "phases": {
            "calibration": calculate(calibration),
            "confirmatory_primary": calculate(confirmatory),
            "stateless_ablation": calculate(stateless_subset),
            "reliability_repeats": calculate(reliability_subset, repetitions=2),
        },
        "provider_calls_made": 0,
    }
