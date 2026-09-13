"""Render corrected prospective prose from frozen structured evidence only."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/grounding-v5-d58-final-design/power.json"
OUTPUT = ROOT / "artifacts/grounding-v5-d58-review-corrections-v2"


def render_report(data: dict[str, Any]) -> str:
    options = data["options"]
    original = min(options, key=lambda row: row["independent_pairs"])
    applicable = [
        row for row in options if row["power"]["upper_sensitivity"] >= data["power_target"]
    ]
    proposed = min(applicable, key=lambda row: row["independent_pairs"]) if applicable else None
    original_status = (
        "meets"
        if original["power"]["observed_discordance"] >= data["power_target"]
        else "falls short of"
    )
    lines = [
        "# D5.8 prospective power and cost check",
        "",
        f"The retained calibration has {data['calibration_discordant_representatives']} discordant outcomes among {data['calibration_independent_representatives']} designated independent representatives. At a {100 * data['minimum_relevant_absolute_difference']:g}-point difference and two-sided exact McNemar alpha {data['alpha']:g}, the original {original['independent_pairs']}-pair proposal {original_status} the {data['power_target']:.0%} power target.",
        "",
        "These calculations use the minimum relevant difference, not the observed calibration effect. They make no confirmatory call and generate no confirmatory task.",
        "",
        "| Independent pairs | Episodes / arm | Power at observed discordance | Power at upper sensitivity | Primary projected USD | Aggregate including repeats USD | Additional reliability episodes |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["options"]:
        lines.append(
            f"| {row['independent_pairs']} | {row['episodes_per_arm']} | "
            f"{row['power']['observed_discordance']:.1%} | {row['power']['upper_sensitivity']:.1%} | "
            f"{Decimal(row['projected_primary_usd']):.2f} | {Decimal(row['projected_aggregate_usd']):.2f} | {row['additional_reliability_episodes']} |"
        )
    q = data["discordance_scenarios"]
    lines.extend(
        [
            "",
            f"Observed discordance is {q['observed_discordance']:.4%}; the Wilson sensitivity endpoints are {q['lower_sensitivity']:.4%} and {q['upper_sensitivity']:.4%}. {data['sensitivity_scope']}",
            "",
            (
                f"The {proposed['independent_pairs']}-pair option has {proposed['power']['observed_discordance']:.1%} power at observed discordance and {proposed['power']['upper_sensitivity']:.1%} at the upper endpoint. At {q['all_discordant_stress']:.0%} discordance it has {proposed['power']['all_discordant_stress']:.1%} power; the target is conditional on the planning assumptions. Keep failures in the primary denominator and report infrastructure causes separately."
                if proposed
                else "No listed option meets the power target at the upper sensitivity endpoint."
            ),
            "",
            f"Cost uses USD {data['cost_basis']['confirmed_repaired_usd']} across {data['cost_basis']['repaired_episode_count']} newly attempted episodes, plus USD {data['cost_basis']['confirmed_aggregate_usd']} already charged. Additional reliability episode counts are recorded separately for each option in the table. {data['cost_basis']['limitations']}",
            "",
            "The owner must select a sample size and planning cap before the final design can be frozen. This report itself authorizes neither resizing nor paid execution.",
            "",
            "[Structured calculation and input hashes](../grounding-v5-d58-final-design/power.json). The calculation sums exact paired-binomial probabilities using the frozen [original audit](../grounding-v5-d58-design/audit.py), consistent with the paired-proportion power framework in [Lachin (1992)](https://onlinelibrary.wiley.com/doi/abs/10.1002/sim.4780110909).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = render_report(json.loads(SOURCE.read_text()))
    binding = {
        "source_file_digests": {
            str(path.relative_to(ROOT)): "sha256:" + sha256_bytes(path.read_bytes())
            for path in (SOURCE, Path(__file__).resolve())
        },
        "provider_calls": 0,
        "original_artifacts_replaced": False,
    }
    values = {
        "power-report-v2.md": report,
        "power-report-binding.json": json.dumps(binding, sort_keys=True, indent=2) + "\n",
    }
    if args.verify:
        for name, value in values.items():
            if (OUTPUT / name).read_text() != value:
                raise ValueError(f"corrected report or binding differs: {name}")
        print("Verified corrected report from stored evidence; provider calls: 0")
    else:
        OUTPUT.mkdir(exist_ok=True)
        if any((OUTPUT / name).exists() for name in values):
            raise FileExistsError("cannot overwrite corrected evidence")
        for name, value in values.items():
            with (OUTPUT / name).open("x") as stream:
                stream.write(value)


if __name__ == "__main__":
    main()
