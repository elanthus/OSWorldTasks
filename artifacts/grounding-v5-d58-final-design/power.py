"""Prospective D5.8 power and cost planning from closed calibration evidence only.

Run with ``python -m artifacts.grounding-v5-d58-final-design.power [--verify]``.
No generator, private journal, provider client, or confirmatory task is invoked.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

POWER = importlib.import_module("artifacts.grounding-v5-d58-design.audit")
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
FINAL = "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json"
PHASES = (
    "artifacts/grounding-v5-d58-reliable-continuation/analysis.json",
    "artifacts/grounding-v5-d58-reliable-extension/analysis.json",
    FINAL,
)


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def wilson_limits(successes: int, n: int) -> tuple[float, float]:
    """Conventional 95% binomial limits used only as a planning sensitivity range."""
    if type(n) is not int or type(successes) is not int or not 0 <= successes <= n or n < 1:
        raise ValueError("invalid binomial counts")
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


def representative_counts(evidence: dict[str, Any]) -> tuple[int, int]:
    paired = evidence["representative_terminal_outcomes"]
    keys = ("both_success", "history_only", "stateless_only", "neither_success")
    counts = [paired[key] for key in keys]
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("invalid representative outcome count")
    n = sum(counts)
    if (
        not evidence["complete"]
        or paired["incomplete"]
        or paired["unpaired_in_subset"]
        or n != evidence["logical_clusters"]
        or n != len(set(evidence["representative_seeds"]))
        or n != len(evidence["representative_seeds"])
    ):
        raise ValueError("representatives are incomplete, duplicated, or inconsistent")
    return n, paired["history_only"] + paired["stateless_only"]


def build_analysis() -> dict[str, Any]:
    evidence = json.loads((ROOT / FINAL).read_text())
    n, discordant = representative_counts(evidence)
    q = discordant / n
    low, high = wilson_limits(discordant, n)
    scenarios = {
        "lower_sensitivity": low,
        "observed_discordance": q,
        "upper_sensitivity": high,
        "all_discordant_stress": 1.0,
    }
    repaired_cost = sum(
        (
            Decimal(json.loads((ROOT / path).read_text())["new_phase_known_spend_usd"])
            for path in PHASES
        ),
        Decimal(0),
    )
    repaired_episodes = sum(
        row["attempted_episodes"] for row in evidence["all_repaired_transport_assignments"].values()
    )
    per_episode = repaired_cost / repaired_episodes
    spent = Decimal(evidence["aggregate_spend"]["spent_usd"])
    rows = []
    for independent in (120, 144, 150, 168, 204):
        episodes_per_arm = independent + 24
        primary_episodes = 2 * episodes_per_arm
        reliability_episodes = 12 * 2 * 2
        primary_cost = per_episode * primary_episodes
        reliability_cost = per_episode * reliability_episodes
        rows.append(
            {
                "independent_pairs": independent,
                "episodes_per_arm": episodes_per_arm,
                "primary_episodes": primary_episodes,
                "additional_reliability_episodes": reliability_episodes,
                "power": {
                    name: POWER.exact_power(independent, 0.20, value)
                    for name, value in scenarios.items()
                },
                "projected_primary_usd": str(primary_cost),
                "projected_reliability_usd": str(reliability_cost),
                "projected_aggregate_usd": str(spent + primary_cost + reliability_cost),
            }
        )
    bindings = (
        FINAL,
        *PHASES[:-1],
        "artifacts/grounding-v5-d58-design/audit.py",
        str(Path(__file__).resolve().relative_to(ROOT)),
    )
    return {
        "schema_version": "pixelgym-d58-prospective-planning-v1",
        "source_file_digests": {path: digest(ROOT / path) for path in bindings},
        "provider_calls": 0,
        "confirmatory_tasks_generated": 0,
        "method": "Unconditional power: M~Bin(n,q), B|M~Bin(M,(q+delta)/(2q)); sum the two-sided exact McNemar rejection probability over M.",
        "method_reference": "https://pubmed.ncbi.nlm.nih.gov/1509223/",
        "alpha": 0.05,
        "minimum_relevant_absolute_difference": 0.20,
        "power_target": 0.80,
        "calibration_independent_representatives": n,
        "calibration_discordant_representatives": discordant,
        "discordance_scenarios": scenarios,
        "sensitivity_scope": "Wilson endpoints are a disclosed planning range, not a guarantee for the fixed family mix or a formal confidence bound on prospective power. No observed-effect power or calibration significance test is reported.",
        "cost_basis": {
            "repaired_episode_count": repaired_episodes,
            "confirmed_repaired_usd": str(repaired_cost),
            "confirmed_mean_usd_per_episode": str(per_episode),
            "confirmed_aggregate_usd": str(spent),
            "unknown_budget_weight_usd": "0",
            "limitations": "Linear projection at historical prices and observed task/arm mix; includes three shortened history episodes and unknown outcomes with owner-assigned zero weight. It is not a request bound, price guarantee, or execution authorization.",
        },
        "options": rows,
        "selected_option": None,
        "paid_execution_authorized": False,
    }


def render_report(data: dict[str, Any]) -> str:
    lines = [
        "# D5.8 prospective power and cost check",
        "",
        f"The retained calibration has {data['calibration_discordant_representatives']} discordant outcomes among {data['calibration_independent_representatives']} designated independent representatives. At a 20-point difference and two-sided exact McNemar alpha 0.05, the original 120-pair proposal falls short of the 80% power target.",
        "",
        "These calculations use the minimum relevant difference, not the observed calibration effect. They make no confirmatory call and generate no confirmatory task.",
        "",
        "| Independent pairs | Episodes / arm | Power at observed discordance | Power at upper sensitivity | Primary projected USD | Aggregate including repeats USD |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["options"]:
        lines.append(
            f"| {row['independent_pairs']} | {row['episodes_per_arm']} | "
            f"{row['power']['observed_discordance']:.1%} | {row['power']['upper_sensitivity']:.1%} | "
            f"{Decimal(row['projected_primary_usd']):.2f} | {Decimal(row['projected_aggregate_usd']):.2f} |"
        )
    q = data["discordance_scenarios"]
    lines.extend(
        [
            "",
            f"Observed discordance is {q['observed_discordance']:.4%}; the conventional 95% Wilson endpoints are {q['lower_sensitivity']:.4%} and {q['upper_sensitivity']:.4%}. {data['sensitivity_scope']}",
            "",
            "The 168-pair option has 85.9% power at observed discordance and 80.3% at the upper endpoint. At 100% discordance it has only 70.0% power; the target is conditional on the planning assumptions. Keep failures in the primary denominator and report infrastructure causes separately.",
            "",
            f"Cost uses USD {data['cost_basis']['confirmed_repaired_usd']} across {data['cost_basis']['repaired_episode_count']} newly attempted episodes, plus USD {data['cost_basis']['confirmed_aggregate_usd']} already charged. Every option includes 48 additional reliability episodes: twelve distinct tasks, two additional trials, two arms. {data['cost_basis']['limitations']}",
            "",
            "The owner must select a sample size and planning cap before the final design can be frozen. This report itself authorizes neither resizing nor paid execution.",
            "",
            "[Structured calculation and input hashes](power.json). The calculation sums exact paired-binomial probabilities using the frozen [original audit](../grounding-v5-d58-design/audit.py), consistent with the paired-proportion power framework in [Lachin (1992)](https://onlinelibrary.wiley.com/doi/abs/10.1002/sim.4780110909).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    data = build_analysis()
    encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
    report = render_report(data)
    if args.verify:
        if (OUTPUT / "power.json").read_text() != encoded:
            raise ValueError("power evidence or input binding mismatch")
        if (OUTPUT / "power.md").read_text() != report:
            raise ValueError("power report differs from structured evidence")
        print("Verified prospective power, cost, input hashes and report; provider calls: 0")
        return
    for filename, value in (("power.json", encoded), ("power.md", report)):
        with (OUTPUT / filename).open("x") as stream:
            stream.write(value)


if __name__ == "__main__":
    main()
