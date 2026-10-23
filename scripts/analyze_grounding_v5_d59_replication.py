"""Apply the frozen D5.9 statistics to the audited, response-free replication projection.

The historical analyzer and its outputs remain unchanged. Statistical calculations
delegate to that analyzer; only the replication bindings and disclosures differ.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import sha256_file
from scripts import analyze_grounding_v5_d59 as frozen
from scripts.report_grounding_v5_d59_replication import OUTPUT, PLAN, ROOT, TOKENS, require

DISCLOSURE = (
    "The replication used the owner-approved v2 stop rule, strict OAuth-token/Max identity, "
    "kernel sandbox, bounded transport retry, and paused active clock during owner login. "
    "These were frozen before this campaign. The separate four-assignment pilot is excluded "
    "from every full-campaign denominator. All 432 full assignments are retained. "
    "One post-send timeout retains unknown provider completion and usage. Its local process "
    "was confirmed stopped; the frozen rule allowed one counted retry on the same request. "
    "Both attempts are retained. Login did not clear this unknown outcome. All local "
    "subprocesses closed. Subscription charge accounting is not a provider billing audit. "
    "The two verified archives are on the same local disk, not independent disaster backups. "
    "No new model call, auth check, task execution, or response rescoring occurred in analysis. "
    "D5.10 and public model-quality wording remain the owner's decisions."
)


def secondary(rows: list[dict]) -> dict:
    groups = {}
    for phase in ("primary", "reliability"):
        groups[phase] = {}
        for mode in ("history", "stateless"):
            subset = [r for r in rows if r["phase"] == phase and r["mode"] == mode]
            choices = [a for r in subset for a in r["first_attempts"]]
            known = sum(r["attempts_with_reported_usage"] for r in subset)
            groups[phase][mode] = {
                "episodes": len(subset),
                "first_attempt_critical_decisions": {
                    "correct": sum(a["correct"] for a in choices),
                    "valid_choice": sum(a["valid_choice"] for a in choices),
                    "denominator": len(choices),
                    "definition": (
                        "first model attempt at each reached consumer, including invalid choices"
                    ),
                    "unreached_consumers_are_not_attempts": True,
                },
                "reached_both_consumers": sum(r["reached_both_consumers"] for r in subset),
                "reported_token_totals": {
                    k: sum(r["reported_tokens"][k] for r in subset) for k in TOKENS
                },
                "attempts_with_reported_usage": known,
                "attempts_without_reported_usage": sum(r["model_attempts"] for r in subset) - known,
                "token_coverage": "reported responses only; timeout usage is unknown",
            }
    return groups


def analyze(projection: dict, plan: dict, historical: dict, *, root: Path = ROOT) -> dict:
    result = frozen.analyze(
        projection,
        plan,
        projection_sha256=sha256_file(root / OUTPUT.relative_to(ROOT) / "report.json"),
        plan_sha256=sha256_file(root / PLAN.relative_to(ROOT)),
        analysis_plan_text=(root / frozen.ANALYSIS_PLAN).read_text(),
        design_doc_text=(root / frozen.DESIGN_DOC).read_text(),
    )
    result["schema_version"] = "pixelgym-d59-replication-confirmatory-analysis-v1"
    result["inputs"]["projection"]["path"] = (OUTPUT.relative_to(ROOT) / "report.json").as_posix()
    result["inputs"]["execution_plan"]["path"] = PLAN.relative_to(ROOT).as_posix()
    policies = result["method"]["primary_comparison"]["policies"]
    policies["historical_pre_registration"] = policies["pre_registered"]
    policies["pre_registered"] = {
        mode: manifest["policy_id"] for mode, manifest in plan["policy_manifests"].items()
    }
    policies["executed_match_pre_registered"] = True
    policies["note"] = (
        "The original quoted Haiku IDs are historical. Replication IDs are bound in the "
        "replication plan frozen before its calls, not substituted into the historical result."
    )
    observed = result["confirmatory"]["paired_absolute_difference"]["estimate"]
    previous = historical["confirmatory"]["paired_absolute_difference"]["estimate"]
    threshold = plan["reporting_rule"]["disagreement_threshold_points"] / 100
    result["historical_comparison"] = {
        "source": (frozen.OUTPUT_JSON).as_posix(),
        "sha256": sha256_file(root / frozen.OUTPUT_JSON),
        "historical_paired_absolute_difference": previous,
        "historical_primary_rates": {
            mode: {"overall": rates["overall"]}
            for mode, rates in historical["success_rates"]["policies"].items()
        },
        "replication_paired_absolute_difference": observed,
        "absolute_difference_between_estimates": abs(previous - observed),
        "frozen_disagreement_threshold": threshold,
        "exceeds_threshold": abs(previous - observed) > threshold,
        "rule": "publish both results and the difference; no post-result selection",
        "not_a_test_of_difference_between_campaigns": True,
    }
    result["secondary"]["retained_journal_diagnostics"] = secondary(projection["results"])
    result["secondary"]["note"] = (
        "Termination profiles use every assigned episode. Request failures are not relabeled "
        "as infrastructure failures; there were no terminal request failures in this full run."
    )
    # Do not invent post-result definitions for named diagnostics absent an operational
    # definition in the freeze. Private traces remain available for separately labelled work.
    result["secondary"]["not_computable_from_public_projection"] = [
        {"diagnostic": name, "reason": reason}
        for name, reason in (
            (
                "dependency retention, recovery, irreversible errors, path overhead, loops",
                (
                    "not estimated: named in the design, but no estimator/denominator was frozen; "
                    "retained traces permit a separately specified exploratory follow-up"
                ),
            ),
            (
                "per-request latency",
                (
                    "no request timing field in the audited canonical projection; "
                    "campaign active and wall runtime are reported separately"
                ),
            ),
            (
                "complete provider token use",
                (
                    "one timeout has unknown provider usage; reported token "
                    "totals cover only the other attempts"
                ),
            ),
        )
    ]
    result["disclosure"] = {
        "text": DISCLOSURE,
        "unresolved_invocations": projection["unresolved_invocations"],
        "unresolved_timeouts": projection["unresolved_timeouts"],
        "assigned": projection["assigned"],
        "completed": projection["completed"],
        "stop_reason": projection["stop_reason"],
        "full_campaign_stop_rule_changed_after_results": False,
    }
    result["human_gate"] = {"D5.10": None, "owner": "project owner"}
    return result


def render(analysis: dict, projection: dict, pilot: dict) -> str:
    text = frozen.render(analysis).replace(
        "# D5.9 Haiku confirmatory analysis", "# D5.9 replication analysis for D5.10 review"
    )
    text = text.replace(
        "`scripts/analyze_grounding_v5_d59.py`",
        "`scripts/analyze_grounding_v5_d59_replication.py` (frozen statistical functions reused)",
    )
    text = text.replace(
        "No private journal was read and no model was called.",
        "The preceding export audited private journals read-only; this analysis reads "
        "only the audited public projection. No model was called.",
    )
    text = text.replace(
        "Quoted from the [pre-registration",
        "Historical definitions quoted from the [pre-registration",
    )
    text = text.replace(
        "## Executed policies",
        "The original Haiku policy IDs quoted above belong to "
        "historical D5.9. The following IDs bind this replication.\n\n## Executed policies",
    )
    comparison = analysis["historical_comparison"]
    historical_rates = comparison["historical_primary_rates"]
    rates = analysis["success_rates"]["policies"]

    def rate_pair(source: dict, mode: str) -> str:
        rate = source[mode]["overall"]
        return f"{rate['successes']}/{rate['denominator']}"

    lines = [
        text.rstrip(),
        "",
        "## Historical D5.9 and the replication",
        "",
        "| Campaign | History successes | Stateless successes | Primary paired difference |",
        "|---|---:|---:|---:|",
        (
            "| [Historical D5.9](../grounding-v5-d59-haiku-results/confirmatory-analysis.md) | "
            f"{rate_pair(historical_rates, 'history')} | "
            f"{rate_pair(historical_rates, 'stateless')} | "
            f"{comparison['historical_paired_absolute_difference']:.6%} |"
        ),
        (
            f"| Replication | {rate_pair(rates, 'history')} | {rate_pair(rates, 'stateless')} | "
            f"{comparison['replication_paired_absolute_difference']:.6%} |"
        ),
        "",
        (
            f"The estimates differ by {comparison['absolute_difference_between_estimates']:.6%}, "
            f"Frozen disagreement threshold: {comparison['frozen_disagreement_threshold']:.0%}; "
            f"exceeded: {comparison['exceeds_threshold']}. "
            "Both results are reported under the pre-registered rule. This descriptive comparison "
            "is not a statistical test of a change between campaigns."
        ),
        "",
        "## Retained-journal diagnostics (exploratory)",
        "",
        (
            "| Phase | Policy | Correct first critical attempts / attempted | "
            "Reached both consumers / episodes | Usage-observed / reserved attempts | "
            "Input tokens | Output tokens |"
        ),
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for phase, modes in analysis["secondary"]["retained_journal_diagnostics"].items():
        for mode, group in modes.items():
            choices = group["first_attempt_critical_decisions"]
            token = group["reported_token_totals"]
            known, unknown = (
                group["attempts_with_reported_usage"],
                group["attempts_without_reported_usage"],
            )
            lines.append(
                f"| {phase} | {mode} | {choices['correct']}/{choices['denominator']} | "
                f"{group['reached_both_consumers']}/{group['episodes']} | "
                f"{known}/{known + unknown} | "
                f"{token['input_tokens']} | {token['output_tokens']} |"
            )
    lines += [
        "",
        (
            "Unreached critical consumers do not enter the first-attempt denominator. Invalid "
            "choices at a reached consumer do. This diagnostic does not replace episode success. "
            "Token totals cover reported responses, with no imputation for the timeout; cache "
            "token counts are separate in the JSON. This is not complete usage or a billing audit."
        ),
        "",
        "## Pilot and accounting",
        "",
        (
            f"The operational pilot completed {pilot['completed']} separate assignments: "
            f"{pilot['provider_processes_started']} attempts/wire requests, "
            f"{sum(v['environment_actions'] for v in pilot['phase_usage'].values())} actions. "
            "Task failures remain in [pilot-report.json](pilot-report.json), never pooled here. "
            "The earlier parser-incompatible pilot remains separately recorded in the "
            "[launch report](../grounding-v5-d59-replication/pilot-launch-report.json)."
        ),
        "",
        (
            f"Full run: {projection['provider_processes_started']} attempts/wire requests; "
            f"{sum(v['environment_actions'] for v in projection['phase_usage'].values())} actions; "
            "zero "
            f"control requests. Active runtime {projection['elapsed_seconds']} s; wall runtime "
            f"{projection['wall_elapsed_seconds']} s; owner-login waiting excluded "
            f"{projection['authentication_wait_excluded_seconds']} s. Approved caps: "
            f"{projection['approved_caps']}; "
            f"{projection['approved_runtime_window_hours']} active hours."
        ),
        "",
        "## Unknown timeout and evidence limits",
        "",
    ]
    for timeout in projection["unresolved_timeouts"]:
        lines += [
            (
                f"`{timeout['trial_id']}`, step {timeout['step_index']}, attempt "
                f"{timeout['attempt_index']}: confirmed stopped locally, provider completion and "
                "usage unknown. Its single retry used the same request digest and both attempts "
                "count against caps. The original timeout remains unresolved in the ledger."
            ),
            "",
        ]
    lines += [
        (
            "Both archive copies were rehashed and their SQLite files checked read-only. "
            "They occupy the same local disk; there is no off-host backup and no new restore "
            "drill in this package. Archive snapshots precede the final receipt appended to the "
            "working summary; manifests bind those earlier snapshots. Raw responses, screenshots, "
            "and private paths stay private. Readers can regenerate analysis and counts, "
            "but cannot replay the private raw evidence from this projection alone."
        ),
        "",
        "D5.10 verdict: `null`. No public model-quality wording is approved by this report.",
        "",
    ]
    return "\n".join(lines)


def build(root: Path = ROOT) -> dict[str, str]:
    output = root / OUTPUT.relative_to(ROOT)
    projection = json.loads((output / "report.json").read_text())
    pilot = json.loads((output / "pilot-report.json").read_text())
    plan = json.loads((root / PLAN.relative_to(ROOT)).read_text())
    require(
        plan["execution_plan_digest"]
        == content_digest(
            {key: value for key, value in plan.items() if key != "execution_plan_digest"}
        ),
        "invalid frozen plan digest",
    )
    require(
        projection["policy_ids"]
        == {mode: manifest["policy_id"] for mode, manifest in plan["policy_manifests"].items()}
        and projection["runtime_identity"] == plan["runtime_identity"],
        "projection identity mismatch",
    )
    require(
        projection["phase"] == "full" and pilot["phase"] == "pilot", "phase separation mismatch"
    )
    require(
        projection["source_artifacts"]["execution-plan.json"]
        == sha256_file(root / PLAN.relative_to(ROOT)),
        "projection plan binding mismatch",
    )
    require(
        projection["execution_plan_digest"]
        == pilot["execution_plan_digest"]
        == plan["execution_plan_digest"],
        "pilot/full plan mismatch",
    )
    historical = json.loads((root / frozen.OUTPUT_JSON).read_text())
    result = analyze(projection, plan, historical, root=root)
    return {
        "confirmatory-analysis.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
        "confirmatory-analysis.md": render(result, projection, pilot),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build()
    for name, data in files.items():
        path = OUTPUT / name
        if args.check:
            require(path.read_text() == data, f"stored analysis differs: {name}")
        else:
            path.write_text(data)
    result = json.loads(files["confirmatory-analysis.json"])
    print(
        json.dumps(
            {
                "confirmatory": result["confirmatory"],
                "historical_comparison": result["historical_comparison"],
                "D5.10": None,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
