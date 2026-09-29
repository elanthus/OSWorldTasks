"""Generate the D5.9 confirmatory analysis from the public result projection.

Reads only committed evidence: the per-assignment projection in
``artifacts/grounding-v5-d59-haiku-results/report.json``, the execution plan that
projection is bound to (for the seed-to-family and robustness-pair mapping and the
declared primary comparison), and the pre-registration record in
``artifacts/grounding-v5-d59-freeze/analysis-plan.md``. No private journal is read
and no model call is made. The Markdown report is rendered only from the JSON.

``--check`` regenerates both files in memory and exits nonzero if either differs
from the committed copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pixelgym.grounding.stats import exact_mcnemar_p_value, wilson_interval
from pixelgym.grounding.v5.confirmatory_analysis import stratified_cluster_bootstrap

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = Path("artifacts/grounding-v5-d59-haiku-results")
PROJECTION = RESULTS_DIR / "report.json"
EXECUTION_PLAN = Path("artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json")
ANALYSIS_PLAN = Path("artifacts/grounding-v5-d59-freeze/analysis-plan.md")
DESIGN_DOC = Path("docs/v5-benchmark-design.md")
EXECUTION_REPORT = RESULTS_DIR / "report.md"
OUTPUT_JSON = RESULTS_DIR / "confirmatory-analysis.json"
OUTPUT_MD = RESULTS_DIR / "confirmatory-analysis.md"

SCHEMA_VERSION = "pixelgym-d59-confirmatory-analysis-v1"
CLASSIFICATIONS = (
    "success_termination",
    "invalid_output",
    "step_limit_truncation",
    "infrastructure_failure",
)
RESOURCE_FIELDS = (
    "model_attempts",
    "provider_wire_requests",
    "provider_control_requests",
    "environment_actions",
)
BOOTSTRAP_METHOD_CHOICES = (
    "Stratum: the workflow family of the task.",
    (
        "Logical cluster: the frozen logical_id; a robustness pair (twin_a, twin_b) is one cluster, "
        "a singleton is its own cluster. A cluster must lie within one family."
    ),
    (
        "Resampling within strata: each family independently draws as many clusters, with "
        "replacement, as it contains; all rows of a drawn cluster enter the resample."
    ),
    (
        "Estimate: the pooled all-episode absolute difference mean(history) - mean(stateless) over "
        "every row in the resample, not an average of per-family differences."
    ),
    "Percentile convention: the frozen v1 linear-interpolation percentile at position (n - 1) * q.",
    (
        "Draw order: one random.Random(seed); per resample, families in ascending key order; within "
        "a family, rng.choice over ascending cluster keys once per original cluster."
    ),
    "Row shape: one row per paired seed with boolean history and stateless outcomes.",
)
NOT_COMPUTABLE = (
    (
        "critical-decision accuracy",
        "needs per-step decision annotations held in the private journals",
    ),
    (
        "dependency retention",
        "needs per-step state and response records held in the private journals",
    ),
    (
        "recovery success",
        "needs error-injection and recovery annotations held in the private journals",
    ),
    (
        "irreversible-error rate",
        "the projection does not distinguish wrong commits from other unsuccessful terminations",
    ),
    (
        "path overhead",
        "the projection carries action totals but not the action trace the diagnostic is defined over",
    ),
    ("loop rate", "needs the per-episode action trace held in the private journals"),
    ("latency", "the projection carries no timing fields"),
    ("tokens", "the projection carries no token counts"),
)
# Pre-registration quotes; each must appear verbatim in its source file.
PREREGISTRATION_QUOTES = (
    (
        ANALYSIS_PLAN,
        "- The single primary hypothesis remains a two-sided exact McNemar comparison at alpha 0.05, with",
    ),
    (ANALYSIS_PLAN, "  a 20-point minimum relevant absolute difference and 80% target power."),
    (
        ANALYSIS_PLAN,
        "- The family-stratified logical-cluster bootstrap keeps seed 20260911, 10,000 resamples, and 95%",
    ),
    (ANALYSIS_PLAN, "  intervals."),
    (ANALYSIS_PLAN, "- Screenshot-history policy: `policy-79441db33362e00a1ac6`."),
    (ANALYSIS_PLAN, "- Current-frame-only policy: `policy-e1d746cd6a83ffb12a20`."),
    (
        ANALYSIS_PLAN,
        "- Primary test: two-sided exact McNemar at alpha 0.05, with the unchanged 20-point minimum",
    ),
    (ANALYSIS_PLAN, "  relevant absolute difference and 80% target power."),
    (
        ANALYSIS_PLAN,
        "- Reliability schedule: two additional trials per arm on the first two independent",
    ),
    (ANALYSIS_PLAN, "  representatives in each workflow family."),
    (
        ANALYSIS_PLAN,
        "  Every singleton and each pair's `twin_a` is a primary representative.",
    ),
    (
        DESIGN_DOC,
        "- **Primary metric:** exact episode success over all assigned confirmatory episodes. Invalid",
    ),
    (
        DESIGN_DOC,
        "  output, request failures, wrong commits, and exhausted budgets stay in the denominator. A missing",
    ),
    (DESIGN_DOC, "  episode makes the run incomplete."),
)


DISCLOSURE_TEXT = (
    "The stop rule was amended after repeated infrastructure failures from Anthropic to allow for "
    "retries in the case of intermittent network issues. Each failed assignment remains in the "
    "results. No prior assignment was replayed."
)


class AnalysisError(ValueError):
    """Raised when the evidence cannot be joined or does not match its bindings."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rate(successes: int, total: int) -> dict[str, Any]:
    low, high = wilson_interval(successes, total)
    return {
        "successes": successes,
        "denominator": total,
        "estimate": successes / total,
        "wilson_95": [low, high],
    }


def seed_mapping(plan: Mapping[str, Any]) -> dict[int, dict[str, str]]:
    """Map each seed to its frozen family, logical cluster, and variant."""
    mapping: dict[int, dict[str, str]] = {}
    for job in [*plan["primary_jobs"], *plan["reliability_jobs"]]:
        record = job["seed_record"]
        entry = {
            "family": str(record["family"]),
            "logical_id": str(record["logical_id"]),
            "variant": str(record["variant"]),
        }
        seed = int(record["seed"])
        if seed != int(job["seed"]):
            raise AnalysisError(f"job seed {job['seed']} disagrees with its seed record {seed}")
        previous = mapping.setdefault(seed, entry)
        if previous != entry:
            raise AnalysisError(f"seed {seed} maps to more than one family or cluster")
    families_by_cluster: dict[str, set[str]] = {}
    for entry in mapping.values():
        families_by_cluster.setdefault(entry["logical_id"], set()).add(entry["family"])
    for cluster, families in families_by_cluster.items():
        if len(families) != 1:
            raise AnalysisError(f"logical cluster {cluster} spans families {sorted(families)}")
    return mapping


def join_rows(
    rows: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Join every projection row to exactly one planned job, family, and cluster."""
    mapping = seed_mapping(plan)
    jobs: dict[tuple[str, str, int, int], Mapping[str, Any]] = {}
    for job in [*plan["primary_jobs"], *plan["reliability_jobs"]]:
        key = (str(job["phase"]), str(job["mode"]), int(job["seed"]), int(job["repeat"]))
        if key in jobs:
            raise AnalysisError(f"execution plan has duplicate job {key}")
        jobs[key] = job
    joined: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for row in rows:
        key = (str(row["phase"]), str(row["mode"]), int(row["seed"]), int(row["repeat"]))
        if key in seen:
            raise AnalysisError(f"projection has duplicate assignment {key}")
        seen.add(key)
        if row["seed"] not in mapping:
            raise AnalysisError(f"seed {row['seed']} has no family in the freeze mapping")
        job = jobs.get(key)
        if job is None:
            raise AnalysisError(f"projection assignment {key} is not in the execution plan")
        if job["trial_id"] != row["trial_id"]:
            raise AnalysisError(f"trial id mismatch for {key}")
        if row["classification"] not in CLASSIFICATIONS:
            raise AnalysisError(f"unknown classification {row['classification']!r}")
        if bool(row["success"]) != (row["classification"] == "success_termination"):
            raise AnalysisError(f"success flag disagrees with classification for {key}")
        joined.append({**row, **mapping[int(row["seed"])]})
    missing = sorted(set(jobs) - seen)
    if missing:
        raise AnalysisError(f"{len(missing)} planned assignments are missing, first {missing[0]}")
    return joined


def paired_table(rows: Sequence[Mapping[str, Any]], seeds: Sequence[int]) -> dict[str, Any]:
    """Paired success table for history vs stateless on identical primary seeds."""
    outcome: dict[tuple[int, str], bool] = {}
    for row in rows:
        if row["phase"] == "primary":
            outcome[(int(row["seed"]), str(row["mode"]))] = bool(row["success"])
    cells = Counter()
    for seed in seeds:
        try:
            pair = (outcome[(seed, "history")], outcome[(seed, "stateless")])
        except KeyError as error:
            raise AnalysisError(f"seed {seed} lacks a primary outcome for both policies") from error
        cells[pair] += 1
    history_only = cells[(True, False)]
    stateless_only = cells[(False, True)]
    return {
        "pairs": len(seeds),
        "both_success": cells[(True, True)],
        "history_only_success": history_only,
        "stateless_only_success": stateless_only,
        "both_failure": cells[(False, False)],
        "exact_mcnemar_two_sided_p": exact_mcnemar_p_value(history_only, stateless_only),
    }


def _consistency(rows: Sequence[Mapping[str, Any]], seeds: Sequence[int]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode in ("history", "stateless"):
        per_seed: dict[int, list[bool]] = {seed: [] for seed in seeds}
        for row in rows:
            if row["mode"] == mode and int(row["seed"]) in per_seed:
                per_seed[int(row["seed"])].append(bool(row["success"]))
        tallies = Counter()
        for seed, outcomes in per_seed.items():
            if len(outcomes) != 3:
                raise AnalysisError(f"seed {seed} {mode} has {len(outcomes)} trials, expected 3")
            tallies[sum(outcomes)] += 1
        result[mode] = {
            "tasks": len(seeds),
            "trials_per_task": 3,
            "all_three_success": tallies[3],
            "two_of_three_success": tallies[2],
            "one_of_three_success": tallies[1],
            "all_three_failure": tallies[0],
            "consistent_tasks": tallies[3] + tallies[0],
            "reliability_phase_only": _rate(
                sum(
                    1
                    for row in rows
                    if row["mode"] == mode and row["phase"] == "reliability" and row["success"]
                ),
                sum(1 for row in rows if row["mode"] == mode and row["phase"] == "reliability"),
            ),
        }
    return result


def _robustness(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode in ("history", "stateless"):
        pairs: dict[str, dict[str, bool]] = {}
        for row in rows:
            if row["phase"] == "primary" and row["mode"] == mode and row["variant"] != "base":
                pairs.setdefault(row["logical_id"], {})[row["variant"]] = bool(row["success"])
        agree = sum(1 for pair in pairs.values() if pair["twin_a"] == pair["twin_b"])
        result[mode] = {
            "pairs": len(pairs),
            "same_outcome": agree,
            "different_outcome": len(pairs) - agree,
        }
    return result


def analyze(
    projection: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    projection_sha256: str,
    plan_sha256: str,
    analysis_plan_text: str,
    design_doc_text: str,
    resamples: int | None = None,
) -> dict[str, Any]:
    comparison = plan["primary_comparison"]
    if list(comparison["conditions"]) != ["history", "stateless"]:
        raise AnalysisError("unexpected primary-comparison conditions")
    rows = join_rows(projection["results"], plan)
    primary = [row for row in rows if row["phase"] == "primary"]
    alpha = float(comparison["alpha"])
    sources = {ANALYSIS_PLAN: analysis_plan_text, DESIGN_DOC: design_doc_text}
    quotes = []
    for path, line in PREREGISTRATION_QUOTES:
        if line not in sources[path].splitlines():
            raise AnalysisError(f"pre-registration line not found in {path}: {line!r}")
        quotes.append({"source": path.as_posix(), "line": line})

    per_policy: dict[str, Any] = {}
    families = sorted({row["family"] for row in primary})
    for mode in ("history", "stateless"):
        mode_rows = [row for row in primary if row["mode"] == mode]
        per_policy[mode] = {
            "overall": _rate(sum(row["success"] for row in mode_rows), len(mode_rows)),
            "per_family": {
                family: _rate(
                    sum(row["success"] for row in mode_rows if row["family"] == family),
                    sum(1 for row in mode_rows if row["family"] == family),
                )
                for family in families
            },
        }

    all_seeds = [int(seed) for seed in comparison["paired_task_seeds"]]
    mapping = seed_mapping(plan)
    representative_seeds = [
        seed for seed in all_seeds if mapping[seed]["variant"] in ("base", "twin_a")
    ]
    table = paired_table(rows, all_seeds)
    representative_table = paired_table(rows, representative_seeds)

    by_seed: dict[int, dict[str, Any]] = {}
    for row in primary:
        entry = by_seed.setdefault(
            int(row["seed"]), {"family": row["family"], "logical_id": row["logical_id"]}
        )
        entry[row["mode"]] = bool(row["success"])
    boot_rows = [by_seed[seed] for seed in all_seeds]
    bootstrap = stratified_cluster_bootstrap(
        boot_rows,
        strata_key="family",
        cluster_key="logical_id",
        seed=int(comparison["bootstrap_seed"]),
        resamples=int(comparison["bootstrap_resamples"]) if resamples is None else resamples,
        level=float(comparison["interval_level"]),
        first_field="history",
        second_field="stateless",
    ).to_dict()

    termination: dict[str, Any] = {}
    resources: dict[str, Any] = {}
    for phase in ("primary", "reliability"):
        for mode in ("history", "stateless"):
            subset = [row for row in rows if row["phase"] == phase and row["mode"] == mode]
            counts = Counter(row["classification"] for row in subset)
            termination.setdefault(phase, {})[mode] = {
                "denominator": len(subset),
                **{name: counts[name] for name in CLASSIFICATIONS},
            }
            resources.setdefault(phase, {})[mode] = {
                "episodes": len(subset),
                **{
                    field: {
                        "total": sum(int(row[field]) for row in subset),
                        "mean_per_episode": sum(int(row[field]) for row in subset) / len(subset),
                    }
                    for field in RESOURCE_FIELDS
                },
            }

    reliability_seeds = [int(seed) for seed in plan["allocation"]["reliability_seeds"]]

    return {
        "schema_version": SCHEMA_VERSION,
        "inputs": {
            "projection": {"path": PROJECTION.as_posix(), "sha256": projection_sha256},
            "execution_plan": {
                "path": EXECUTION_PLAN.as_posix(),
                "sha256": plan_sha256,
                "execution_plan_digest": plan["execution_plan_digest"],
            },
            "pre_registration": ANALYSIS_PLAN.as_posix(),
        },
        "method": {
            "pre_registration_quotes": quotes,
            "primary_comparison": {
                "policies": {
                    "history": "policy-79441db33362e00a1ac6",
                    "stateless": "policy-e1d746cd6a83ffb12a20",
                },
                "phase": "primary",
                "difference_direction": "history minus stateless",
                "test": comparison["test"],
                "alpha": alpha,
                "minimum_relevant_absolute_difference": comparison[
                    "minimum_relevant_absolute_difference"
                ],
                "target_power": comparison["target_power"],
                "paired_on": "identical seed, phase primary, repeat 0",
                "paired_sample": (
                    "all paired_task_seeds in the execution plan's primary_comparison "
                    "(every assigned primary episode)"
                ),
            },
            "bootstrap": {
                "estimator": "pixelgym.grounding.v5.confirmatory_analysis.stratified_cluster_bootstrap",
                "seed": comparison["bootstrap_seed"],
                "resamples": comparison["bootstrap_resamples"],
                "level": comparison["interval_level"],
                "interpretation_choices_accepted_by_owner": list(BOOTSTRAP_METHOD_CHOICES),
            },
            "mcnemar_estimator": "pixelgym.grounding.stats.exact_mcnemar_p_value",
            "interval_estimator": "pixelgym.grounding.stats.wilson_interval",
            "denominator_rule": (
                "every assigned primary episode, including invalid output, step-limit "
                "truncation, and infrastructure failure"
            ),
        },
        "confirmatory": {
            "preregistered": True,
            "paired_success_table": table,
            "p_value_below_alpha": table["exact_mcnemar_two_sided_p"] < alpha,
            "paired_absolute_difference": bootstrap,
        },
        "success_rates": {
            "preregistered": False,
            "label": "exploratory",
            "note": (
                "Wilson intervals are listed in the v5 design analysis plan but not in the "
                "pre-registration record."
            ),
            "phase": "primary",
            "policies": per_policy,
        },
        "independent_representative_subset": {
            "preregistered": False,
            "label": "exploratory",
            "note": (
                "The same test restricted to singletons and twin_a, the frozen independent "
                "representative rule. The pre-registration does not name this subset as a test."
            ),
            "paired_success_table": representative_table,
        },
        "reliability": {
            "preregistered": True,
            "note": "Reported separately; never pooled with the primary comparison.",
            "seeds": reliability_seeds,
            "policies": _consistency(
                [row for row in rows if int(row["seed"]) in set(reliability_seeds)],
                reliability_seeds,
            ),
        },
        "secondary": {
            "preregistered": False,
            "label": "exploratory",
            "termination_profile": termination,
            "resource_use": resources,
            "robustness_consistency": _robustness(rows),
            "note": (
                "Invalid-output and request-failure rates are the invalid_output and "
                "infrastructure_failure columns of the termination profile."
            ),
            "not_computable_from_public_projection": [
                {"diagnostic": name, "reason": reason} for name, reason in NOT_COMPUTABLE
            ],
        },
        "disclosure": {
            "text": DISCLOSURE_TEXT,
            "stop_rule_changed": True,
            "unresolved_invocations": int(projection["unresolved_invocations"]),
            "stop_reason": projection["stop_reason"],
            "assigned": int(projection["assigned"]),
            "completed": int(projection["completed"]),
        },
        "human_gate": "D5.10 is the owner's review.",
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _rate_cells(rate: Mapping[str, Any]) -> str:
    low, high = rate["wilson_95"]
    return (
        f"{rate['successes']} | {rate['denominator']} | {_pct(rate['estimate'])} | "
        f"{_pct(low)} to {_pct(high)}"
    )


def _table_md(table: Mapping[str, Any]) -> list[str]:
    return [
        "| | Stateless success | Stateless failure |",
        "|---|---:|---:|",
        f"| History success | {table['both_success']} | {table['history_only_success']} |",
        f"| History failure | {table['stateless_only_success']} | {table['both_failure']} |",
    ]


def render(analysis: Mapping[str, Any]) -> str:
    method = analysis["method"]
    comparison = method["primary_comparison"]
    confirm = analysis["confirmatory"]
    table = confirm["paired_success_table"]
    diff = confirm["paired_absolute_difference"]
    alpha = comparison["alpha"]
    below = "below" if confirm["p_value_below_alpha"] else "not below"
    lines = [
        "# D5.9 Haiku confirmatory analysis",
        "",
        (
            "Generated by `scripts/analyze_grounding_v5_d59.py` from [report.json](report.json) and the "
            "frozen execution plan. No private journal was read and no model was called. "
            "D5.10 is the owner's review."
        ),
        "",
        "## Pre-registered definitions",
        "",
        (
            "Quoted from the [pre-registration record](../grounding-v5-d59-freeze/analysis-plan.md) "
            "and the v5 design analysis plan:"
        ),
        "",
        "```text",
        *[quote["line"] for quote in method["pre_registration_quotes"]],
        "```",
        "",
        (
            f"The primary comparison is {comparison['difference_direction']}, primary phase only, "
            f"paired on {comparison['paired_on']}, {comparison['test']} at alpha {alpha}. "
            f"Every assigned episode stays in the denominator."
        ),
        "",
        "## Primary comparison (pre-registered)",
        "",
        f"Paired success table over {table['pairs']} primary seeds:",
        "",
        *_table_md(table),
        "",
        (
            f"Exact McNemar two-sided p = {table['exact_mcnemar_two_sided_p']:.3g}, "
            f"which is {below} the pre-registered alpha of {alpha}."
        ),
        "",
        (
            f"Paired absolute success difference ({comparison['difference_direction']}): "
            f"{_pct(diff['estimate'])}. Family-stratified logical-cluster bootstrap "
            f"{int(diff['level'] * 100)}% interval: {_pct(diff['interval'][0])} to "
            f"{_pct(diff['interval'][1])} (seed {diff['seed']}, {diff['resamples']} resamples, "
            f"{diff['strata']} families, {diff['clusters']} clusters, {diff['rows']} paired seeds). "
            f"The pre-registered minimum relevant absolute difference is "
            f"{_pct(comparison['minimum_relevant_absolute_difference'])}."
        ),
        "",
        "## Success rates, primary phase (exploratory)",
        "",
        analysis["success_rates"]["note"],
        "",
        "| Policy | Scope | Successes | Denominator | Estimate | Wilson 95% |",
        "|---|---|---:|---:|---:|---|",
    ]
    for mode, rates in analysis["success_rates"]["policies"].items():
        lines.append(f"| {mode} | overall | {_rate_cells(rates['overall'])} |")
        for family, rate in rates["per_family"].items():
            lines.append(f"| {mode} | {family} | {_rate_cells(rate)} |")
    subset = analysis["independent_representative_subset"]
    sub_table = subset["paired_success_table"]
    lines += [
        "",
        "## Independent representatives only (exploratory)",
        "",
        subset["note"],
        "",
        *_table_md(sub_table),
        "",
        (
            f"Exact McNemar two-sided p = {sub_table['exact_mcnemar_two_sided_p']:.3g} over "
            f"{sub_table['pairs']} pairs."
        ),
        "",
        "## Reliability subset (pre-registered, reported separately)",
        "",
        (
            f"{analysis['reliability']['note']} Seeds: "
            f"{', '.join(str(seed) for seed in analysis['reliability']['seeds'])}. Each task has the "
            "primary trial and two reliability trials."
        ),
        "",
        "| Policy | Tasks | 3 of 3 | 2 of 3 | 1 of 3 | 0 of 3 | Reliability-phase successes |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, rel in analysis["reliability"]["policies"].items():
        phase = rel["reliability_phase_only"]
        lines.append(
            f"| {mode} | {rel['tasks']} | {rel['all_three_success']} | "
            f"{rel['two_of_three_success']} | {rel['one_of_three_success']} | "
            f"{rel['all_three_failure']} | {phase['successes']} of {phase['denominator']} |"
        )
    secondary = analysis["secondary"]
    lines += [
        "",
        "## Termination profile (exploratory)",
        "",
        "| Phase | Policy | Episodes | Success | Invalid output | Step limit | Infrastructure |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for phase, modes in secondary["termination_profile"].items():
        for mode, counts in modes.items():
            lines.append(
                f"| {phase} | {mode} | {counts['denominator']} | "
                f"{counts['success_termination']} | {counts['invalid_output']} | "
                f"{counts['step_limit_truncation']} | {counts['infrastructure_failure']} |"
            )
    lines += [
        "",
        secondary["note"],
        "",
        "## Resource use (exploratory)",
        "",
        "Totals, with the mean per episode in parentheses.",
        "",
        (
            "| Phase | Policy | Model attempts | Provider wire requests | "
            "Provider control requests | Environment actions |"
        ),
        "|---|---|---:|---:|---:|---:|",
    ]
    for phase, modes in secondary["resource_use"].items():
        for mode, usage in modes.items():
            cells = " | ".join(
                f"{usage[field]['total']} ({usage[field]['mean_per_episode']:.2f})"
                for field in RESOURCE_FIELDS
            )
            lines.append(f"| {phase} | {mode} | {cells} |")
    lines += [
        "",
        "## Robustness-pair consistency (exploratory)",
        "",
        "| Policy | Pairs | Same outcome | Different outcome |",
        "|---|---:|---:|---:|",
    ]
    for mode, rob in secondary["robustness_consistency"].items():
        lines.append(
            f"| {mode} | {rob['pairs']} | {rob['same_outcome']} | {rob['different_outcome']} |"
        )
    lines += [
        "",
        "## Diagnostics not computable from the public projection",
        "",
        "These are not estimated.",
        "",
    ]
    for item in secondary["not_computable_from_public_projection"]:
        lines.append(f"- {item['diagnostic']}: {item['reason']}.")
    disclosure = analysis["disclosure"]
    lines += [
        "",
        "## Disclosure",
        "",
        disclosure["text"],
        "",
    ]
    return "\n".join(lines)


def _dumps(analysis: Mapping[str, Any]) -> str:
    return json.dumps(analysis, indent=2, sort_keys=True) + "\n"


def build(root: Path = ROOT) -> tuple[str, str]:
    projection_bytes = (root / PROJECTION).read_bytes()
    plan_bytes = (root / EXECUTION_PLAN).read_bytes()
    projection = json.loads(projection_bytes)
    plan = json.loads(plan_bytes)
    plan_sha = _sha256(plan_bytes)
    bound = projection["source_artifacts"]["execution-plan.json"]
    if bound != plan_sha:
        raise AnalysisError(f"projection is bound to execution plan {bound}, found {plan_sha}")
    analysis = analyze(
        projection,
        plan,
        projection_sha256=_sha256(projection_bytes),
        plan_sha256=plan_sha,
        analysis_plan_text=(root / ANALYSIS_PLAN).read_text(encoding="utf-8"),
        design_doc_text=(root / DESIGN_DOC).read_text(encoding="utf-8"),
    )
    return _dumps(analysis), render(analysis)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed outputs")
    args = parser.parse_args(argv)
    json_text, md_text = build()
    if args.check:
        stale = [
            path.as_posix()
            for path, text in ((OUTPUT_JSON, json_text), (OUTPUT_MD, md_text))
            if not (ROOT / path).exists() or (ROOT / path).read_text(encoding="utf-8") != text
        ]
        if stale:
            print("stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    (ROOT / OUTPUT_JSON).write_text(json_text, encoding="utf-8")
    (ROOT / OUTPUT_MD).write_text(md_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
