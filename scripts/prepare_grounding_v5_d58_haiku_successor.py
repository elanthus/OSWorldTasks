"""Build the response-free Haiku successor to the D5.8 power calculation."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/grounding-v5-d58-haiku-successor"
SNAPSHOT = ROOT / "artifacts/grounding-v5-haiku-cli-replication/snapshot.json"
SOURCES = ROOT / "artifacts/grounding-v5-haiku-cli-replication/sources.json"
REPRESENTATIVES = ROOT / "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json"
POWER = importlib.import_module("artifacts.grounding-v5-d58-final-design.power")
OPTIONS = (120, 144, 150, 168, 204)
TERMINAL = {"success_termination", "step_limit_truncation", "invalid_output"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def representative_outcomes(snapshot: dict[str, Any], seeds: list[int]) -> dict[str, Any]:
    """Return paired outcomes for the predeclared logical representatives."""
    if len(seeds) != len(set(seeds)) or len(seeds) != 44:
        raise ValueError("representative allocation must contain 44 unique seeds")
    wanted = set(seeds)
    by_seed: dict[int, dict[str, dict[str, Any]]] = {}
    for row in snapshot["results"]:
        if row["seed"] not in wanted:
            continue
        mode = row["mode"]
        if mode not in {"history", "stateless"} or mode in by_seed.setdefault(row["seed"], {}):
            raise ValueError("duplicate or unknown representative arm")
        if row["classification"] not in TERMINAL:
            raise ValueError("representative has a nonterminal outcome")
        if row["success"] != (row["classification"] == "success_termination"):
            raise ValueError("representative success disagrees with terminal classification")
        by_seed[row["seed"]][mode] = row
    if set(by_seed) != wanted or any(set(pair) != {"history", "stateless"} for pair in by_seed.values()):
        raise ValueError("representative outcomes are incomplete")

    counts = Counter()
    rows = []
    for seed in seeds:
        history = by_seed[seed]["history"]["success"]
        stateless = by_seed[seed]["stateless"]["success"]
        outcome = (
            "both_success"
            if history and stateless
            else "history_only"
            if history
            else "stateless_only"
            if stateless
            else "neither_success"
        )
        counts[outcome] += 1
        rows.append({"seed": seed, "outcome": outcome})
    return {
        "pairs": len(rows),
        "outcomes": {
            name: counts[name]
            for name in ("both_success", "history_only", "stateless_only", "neither_success")
        },
        "discordant": counts["history_only"] + counts["stateless_only"],
        "rows": rows,
    }


def build_analysis() -> dict[str, Any]:
    snapshot = _read(SNAPSHOT)
    sources = _read(SOURCES)
    representative_source = _read(REPRESENTATIVES)
    if _digest(SNAPSHOT) != sources["snapshot_sha256"]:
        raise ValueError("Haiku snapshot digest mismatch")
    if snapshot["plan_digest"] != sources["plan_digest"]:
        raise ValueError("Haiku execution-plan binding mismatch")
    if snapshot["completed"] != 100 or snapshot["unrun"] != 0:
        raise ValueError("Haiku calibration is incomplete")

    paired = representative_outcomes(snapshot, representative_source["representative_seeds"])
    q = paired["discordant"] / paired["pairs"]
    lower, upper = POWER.wilson_limits(paired["discordant"], paired["pairs"])
    scenarios = {
        "lower_sensitivity": lower,
        "observed_discordance": q,
        "upper_sensitivity": upper,
        "all_discordant_stress": 1.0,
    }
    options = [
        {
            "independent_pairs": count,
            "episodes_per_arm_with_robustness_twins": count + 24,
            "power": {
                name: POWER.POWER.exact_power(count, 0.20, discordance)
                for name, discordance in scenarios.items()
            },
        }
        for count in OPTIONS
    ]
    manifests = snapshot["policy_manifests"]
    sandbox_applied = {
        mode: manifest["sandbox"]["runtime_enforcement"]["os_sandbox_applied"]
        for mode, manifest in manifests.items()
    }
    return {
        "schema_version": "pixelgym-d58-haiku-successor-analysis-v1",
        "source_file_digests": {
            str(path.relative_to(ROOT)): _digest(path)
            for path in (
                SNAPSHOT,
                SOURCES,
                REPRESENTATIVES,
                ROOT / "artifacts/grounding-v5-d58-design/audit.py",
                ROOT / "artifacts/grounding-v5-d58-final-design/power.py",
                Path(__file__),
            )
        },
        "provider_calls_made": 0,
        "confirmatory_tasks_generated": 0,
        "calibration": {
            "model": snapshot["runtime_identity"]["requested_model"],
            "provider": manifests["history"]["provider"],
            "cli_version": snapshot["runtime_identity"]["cli_version"],
            "plan_digest": snapshot["plan_digest"],
            "history_policy_id": manifests["history"]["policy_id"],
            "stateless_policy_id": manifests["stateless"]["policy_id"],
            "all_pairs": snapshot["fresh_cohort"]["conditions"]["history"],
            "independent_representatives": paired,
        },
        "method": "Unconditional power: M~Bin(n,q), B|M~Bin(M,(q+delta)/(2q)); sum the two-sided exact McNemar rejection probability over M.",
        "method_reference": "https://pubmed.ncbi.nlm.nih.gov/1509223/",
        "alpha": 0.05,
        "minimum_relevant_absolute_difference": 0.20,
        "power_target": 0.80,
        "discordance_scenarios": scenarios,
        "sensitivity_scope": "Wilson endpoints are a disclosed planning range, not a guarantee for the fixed family mix or a formal confidence bound on prospective power. No observed-effect power or calibration significance test is reported.",
        "options": options,
        "carry_forward_candidate": {
            "independent_pairs": 168,
            "episodes_per_arm_with_robustness_twins": 192,
            "meets_power_at_observed_discordance": options[3]["power"]["observed_discordance"]
            >= 0.80,
            "meets_power_at_upper_sensitivity": options[3]["power"]["upper_sensitivity"]
            >= 0.80,
            "status": "analytical candidate; owner selection not recorded",
        },
        "execution_readiness": {
            "os_sandbox_applied": sandbox_applied,
            "confirmatory_ready": False,
            "reason": "The calibration manifests record that OS sandbox enforcement was not applied to either Claude Code CLI launch. A confirmatory successor also requires newly frozen caps, runtime, admission, and exact approval.",
        },
        "owner_selection": None,
        "paid_or_subscription_execution_authorized": False,
    }


def render_report(data: dict[str, Any]) -> str:
    paired = data["calibration"]["independent_representatives"]
    outcomes = paired["outcomes"]
    lines = [
        "# Haiku D5.8 successor power check",
        "",
        "This response-free successor recomputes the D5.8 paired-power inputs from the fresh Haiku Claude Code CLI calibration. It uses the 44 representatives selected before the Haiku outcomes were observed; it does not treat all 50 seed pairs as independent.",
        "",
        "## Independent calibration outcomes",
        "",
        f"The representatives contain {outcomes['both_success']} both-success, {outcomes['history_only']} history-only, {outcomes['stateless_only']} stateless-only, and {outcomes['neither_success']} neither-success pairs. That is {paired['discordant']}/{paired['pairs']} discordant representatives ({data['discordance_scenarios']['observed_discordance']:.4%}).",
        "",
        "At the unchanged 20-point minimum relevant difference, two-sided exact McNemar alpha 0.05, and 80% target power:",
        "",
        "| Independent pairs | Episodes / arm with 24 twins | Power at observed discordance | Power at upper sensitivity | All-discordant stress |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in data["options"]:
        lines.append(
            f"| {row['independent_pairs']} | {row['episodes_per_arm_with_robustness_twins']} | "
            f"{row['power']['observed_discordance']:.1%} | "
            f"{row['power']['upper_sensitivity']:.1%} | "
            f"{row['power']['all_discordant_stress']:.1%} |"
        )
    scenarios = data["discordance_scenarios"]
    lines.extend(
        [
            "",
            f"The conventional 95% Wilson planning range is {scenarios['lower_sensitivity']:.4%}–{scenarios['upper_sensitivity']:.4%}. {data['sensitivity_scope']}",
            "",
            "The prior 168-independent-pair choice remains an analytical candidate: it yields 87.0% power at observed discordance and 81.2% at the upper sensitivity endpoint. This recomputation does not carry forward the prior Gemini owner selection automatically.",
            "",
            "## Remaining boundary",
            "",
            "No provider call was made and no confirmatory task was generated. Owner selection remains unset. D5.9 is not ready to execute: the calibration manifests record that OS sandbox enforcement was not applied to either Claude Code CLI launch, and a Haiku successor still needs frozen admission, caps, runtime, and exact execution approval.",
            "",
            "The structured analysis records every representative seed and source digest in [`analysis.json`](analysis.json).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    analysis = build_analysis()
    encoded = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    report = render_report(analysis)
    if args.verify:
        if (OUTPUT / "analysis.json").read_text() != encoded:
            raise ValueError("Haiku successor analysis differs from stored evidence")
        if (OUTPUT / "report.md").read_text() != report:
            raise ValueError("Haiku successor report differs from stored evidence")
        print("Verified Haiku D5.8 successor power evidence; provider calls: 0")
        return
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "analysis.json").write_text(encoded)
    (OUTPUT / "report.md").write_text(report)


if __name__ == "__main__":
    main()
