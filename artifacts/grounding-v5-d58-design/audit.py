"""Reproduce the D5.8 no-call structural audit and prospective power scenarios.

Run from the repository root: .venv/bin/python -m artifacts.grounding-v5-d58-design.audit
No confirmatory task is generated, captured, or evaluated by this audit.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from functools import cache
from pathlib import Path

from pixelgym.grounding.v5.contracts import StageKind
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.seeds import (
    CALIBRATION_SEEDS,
    D56_REPLACEMENT_CALIBRATION_SEEDS,
    DEVELOPMENT_SEEDS,
)


@cache
def rejection_cutoff(discordant: int) -> int:
    """Largest lower-tail count rejected by two-sided exact McNemar, alpha .05."""
    cumulative = 0.0
    cutoff = -1
    for count in range(discordant // 2 + 1):
        cumulative += math.comb(discordant, count) * 0.5**discordant
        if min(1.0, 2 * cumulative) > 0.05:
            break
        cutoff = count
    return cutoff


def exact_power(n: int, difference: float, discordance: float) -> float:
    """Sum over M~Bin(n,q), B|M~Bin(M,(q+delta)/(2q)); independent units only."""
    if n < 1 or not 0 < discordance <= 1 or abs(difference) > discordance:
        raise ValueError("invalid prospective paired-binomial parameters")
    direction = (discordance + difference) / (2 * discordance)
    power = 0.0
    for total in range(n + 1):
        cutoff = rejection_cutoff(total)
        if cutoff < 0:
            continue
        mass = (
            math.comb(n, total)
            * discordance**total
            * (1 - discordance) ** (n - total)
        )
        conditional = sum(
            math.comb(total, count)
            * (
                direction**count * (1 - direction) ** (total - count)
                + direction ** (total - count) * (1 - direction) ** count
            )
            for count in range(cutoff + 1)
        )
        power += mass * conditional
    return power


def main() -> None:
    # Independent checks: six all-favorable discordances are the first rejection;
    # with no adverse discordances, power is exactly Pr[Bin(n, delta) >= 6].
    assert rejection_cutoff(5) == -1
    assert rejection_cutoff(6) == 0
    tail = sum(math.comb(24, m) * 0.2**m * 0.8 ** (24 - m) for m in range(6, 25))
    assert math.isclose(exact_power(24, 0.2, 0.2), tail, abs_tol=1e-12)
    assert exact_power(72, 0, 0.4) <= 0.05
    assert math.isclose(exact_power(72, 0.2, 0.4), exact_power(72, -0.2, 0.4))

    manifest_path = Path("artifacts/grounding-v5-manifests/v2/confirmatory.json")
    calibration_path = Path("artifacts/grounding-v5-manifests/v2/calibration-d56.json")
    qwen_path = Path("artifacts/grounding-v5-calibration-supplement/qwen-pair.json")
    manifest = json.loads(manifest_path.read_text())
    admitted_calibration = {
        row["seed_record"]["seed"]
        for row in json.loads(calibration_path.read_text())["records"]
    }
    qwen = json.loads(qwen_path.read_text())
    positions: dict[str, Counter[int]] = {}
    duplicate_labels = []
    rows = []
    for seed in DEVELOPMENT_SEEDS + CALIBRATION_SEEDS + D56_REPLACEMENT_CALIBRATION_SEEDS:
        task = generate_task(seed)
        variant = task.seed_record.variant
        counts = positions.setdefault(variant, Counter())
        for stage in task.stages:
            if stage.kind is StageKind.TEXT:
                continue
            counts.update(
                [next(i for i, c in enumerate(stage.controls)
                      if c.control_id == stage.target_control_id)]
            )
            labels = [control.label for control in stage.controls]
            if len(set(labels)) != len(labels):
                duplicate_labels.append({"seed": seed, "stage": stage.stage_id})
        rows.append({"seed": seed, "task_id": task.task_id, "variant": variant})

    records = manifest["records"]
    families = list(dict.fromkeys(row["seed_record"]["family"] for row in records))

    def summarize(selected: list[dict]) -> dict:
        return {
            "episodes": len(selected),
            "logical_clusters": len({row["seed_record"]["logical_id"] for row in selected}),
            "environment_action_cap_one_trial": sum(row["max_episode_steps"] for row in selected),
            "seeds": [row["seed_record"]["seed"] for row in selected],
        }

    subsets = {"primary": summarize(records)}
    for name, size in (("stateless_ablation", 4), ("reliability_subset", 2)):
        subsets[name] = summarize([
            row for family in families
            for row in [r for r in records if r["seed_record"]["family"] == family][:size]
        ])
    sources = [
        Path(__file__).relative_to(Path.cwd()), manifest_path, calibration_path, qwen_path,
        *[Path("pixelgym/grounding/v5") / name for name in (
            "generator.py", "contracts.py", "seeds.py", "policies.py",
            "panel_policy.py", "planning.py",
        )],
    ]
    result = {
        "schema_version": "pixelgym-v5-d58-design-audit-v1",
        "status": "decision_support_only_not_a_human_gate_or_execution_plan",
        "provider_calls": 0,
        "confirmatory_tasks_generated_or_evaluated": 0,
        "power_self_checks": 5,
        "source_sha256": {
            str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        },
        "structural_audit": {
            "task_count": len(rows),
            "tasks": rows,
            "correct_control_zero_based_positions": positions,
            "duplicate_visible_control_labels": duplicate_labels,
            "duplicate_label_seeds_in_current_d56": sorted({
                row["seed"] for row in duplicate_labels if row["seed"] in admitted_calibration
            }),
            "limitation": "Privileged structural inspection; not a pixel-only policy rollout.",
        },
        "qwen_published_receipt": {
            "attempted": qwen["summary_without_transport_records"]["attempted_policy_task_pairs"],
            "successful": qwen["summary_without_transport_records"]["successful_policy_task_pairs"],
            "decision_exposure": qwen["local_audit_receipt"]["decision_exposure"],
            "limitation": "Stored audit receipt; restricted journal was not independently reaudited.",
        },
        "existing_confirmatory_manifest": {
            "manifest_digest": manifest["manifest_digest"],
            "subsets": subsets,
            "bands": Counter(row["seed_record"]["difficulty_band"] for row in records),
        },
        "power_method": {
            "test": "two-sided exact conditional McNemar; prospective unconditional power",
            "alpha": 0.05,
            "minimum_relevant_absolute_difference": 0.2,
            "target_power": 0.8,
            "assumption": "Independent paired logical-task outcomes; q is hypothetical, not estimated from Qwen.",
            "not_established": "Power for episode-weighted estimates with correlated twins, new policies, or a revised generator.",
        },
        "power_scenarios": [
            {"discordance": q, "power_by_independent_units": {
                str(n): exact_power(n, 0.2, q) for n in (12, 24, 72, 96, 120)
            }, "first_n_with_80_percent_power_in_6_to_180": next(
                n for n in range(6, 181) if exact_power(n, 0.2, q) >= 0.8
            )}
            for q in (0.2, 0.3, 0.4, 0.5)
        ],
    }
    output = Path(__file__).relative_to(Path.cwd()).with_name("no-call-audit.json")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"artifact": str(output), "power_self_checks": 5,
                      "audited_tasks": len(rows), "provider_calls": 0}))


if __name__ == "__main__":
    main()
