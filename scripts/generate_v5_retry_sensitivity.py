#!/usr/bin/env python3
"""Post hoc: v5 Haiku arm results as recorded and with every retried episode counted as a failure.

Reads the three campaigns' per-episode ``report.json`` files and the execution plans they bind, and
writes ``artifacts/grounding-v5-retry-sensitivity-v1.json`` plus a Markdown rendering. Frozen
inputs are not modified. Standard library only; no randomness; no model call.

This analysis was written on 2026-11-15, after all three campaigns' outcomes were known. It is post
hoc. It reports two numbers per arm, the recorded result and a sensitivity in which any episode that
used a transport retry counts as a failure, and does not choose between them.

Where the retries come from
---------------------------

Every executed v5 Haiku plan allows at most two model attempts per action
(``max_model_attempts_per_action == 2``), so one counted retry per action. Retries are identified in
two ways:

- ``recorded``: the replication and parser-sensitivity reports list every retried post-send
  timeout under ``unresolved_timeouts`` (``counted_attempts_for_action == 2``), one row per retry.
- ``inferred``: the historical D5.9 journals were not retained (see ``docs/evidence-index.md``,
  2026-09-30), so its retries are inferred from per-episode counters. An episode that ends on an
  invalid output or an infrastructure failure has one undispatched send on its final step that is
  not a retry (an invalid output is never retried; an infrastructure failure is a second transport
  error after its one retry). Every other surplus model attempt over dispatched environment
  actions is a retry::

      retries = model_attempts - environment_actions - (1 if final step undispatched else 0)

The inference is applied to all three campaigns, and the generator refuses to write unless it
reproduces the recorded retries exactly, trial by trial, for the two campaigns that record them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pixelgym-v5-retry-sensitivity-v1"
WRITTEN_ON = "2026-11-15"
CAMPAIGNS = (
    (
        "historical",
        "artifacts/grounding-v5-d59-haiku-results/report.json",
        "artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json",
    ),
    (
        "replication",
        "artifacts/grounding-v5-d59-replication-results/report.json",
        "artifacts/grounding-v5-d59-replication/execution-plan.json",
    ),
    (
        "parser_sensitivity",
        "artifacts/grounding-v5-d59-action-extraction-results/report.json",
        "artifacts/grounding-v5-d59-action-extraction-successor/execution-plan.json",
    ),
)
JOURNAL_NOT_RETAINED = {
    "historical": "docs/evidence-index.md (2026-09-30, D5.9 private journals)",
}
JSON_OUTPUT = "artifacts/grounding-v5-retry-sensitivity-v1.json"
MD_OUTPUT = "artifacts/grounding-v5-retry-sensitivity-v1.md"
ARMS = ("history", "stateless")
PHASES = ("primary", "reliability")
FINAL_STEP_UNDISPATCHED = frozenset({"invalid_output", "infrastructure_failure"})
UNRECORDED_CLASS = "not recorded (journal not retained)"


class RetrySensitivityError(ValueError):
    """Raised when the stored inputs do not have the expected structure or disagree."""


def inferred_retries(result: dict[str, Any]) -> int:
    """Retried sends in one episode, from its counters (see module docstring)."""

    surplus = result["model_attempts"] - result["environment_actions"]
    if result["classification"] in FINAL_STEP_UNDISPATCHED:
        surplus -= 1
    if surplus < 0:
        raise RetrySensitivityError(f"{result['trial_id']}: fewer attempts than the rule allows")
    return surplus


def _retry_rule(plan: dict[str, Any]) -> dict[str, Any]:
    manifests = plan["policy_manifests"]
    rules = {arm: manifests[arm]["transport_retry_rule"] for arm in ARMS}
    limits = {arm: manifests[arm]["max_model_attempts_per_action"] for arm in ARMS}
    if len(set(rules.values())) != 1 or set(limits.values()) != {2}:
        raise RetrySensitivityError("arms must share one retry rule with two attempts per action")
    return {"max_model_attempts_per_action": 2, "transport_retry_rule": rules["history"]}


def campaign_summary(name: str, report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if report["execution_plan_digest"] != plan["execution_plan_digest"]:
        raise RetrySensitivityError(f"{name}: report does not bind the given execution plan")
    results = report["results"]
    by_trial = {row["trial_id"]: row for row in results}
    if len(by_trial) != len(results):
        raise RetrySensitivityError(f"{name}: duplicate trial ids")

    inferred = {row["trial_id"]: inferred_retries(row) for row in results}
    inferred = {trial: count for trial, count in inferred.items() if count}

    recorded_rows = report.get("unresolved_timeouts")
    if recorded_rows is None:
        if name not in JOURNAL_NOT_RETAINED:
            raise RetrySensitivityError(f"{name}: no retry records and no disclosed journal loss")
        source = "inferred"
        classes: dict[str, list[str]] = {
            trial: [UNRECORDED_CLASS] * n for trial, n in inferred.items()
        }
    else:
        source = "recorded"
        recorded: Counter[str] = Counter()
        classes = {}
        for row in recorded_rows:
            if row["counted_attempts_for_action"] != 2 or row["trial_id"] not in by_trial:
                raise RetrySensitivityError(f"{name}: unexpected retry record {row['trial_id']}")
            recorded[row["trial_id"]] += 1
            classes.setdefault(row["trial_id"], []).append(row["status"])
        if dict(recorded) != inferred:
            raise RetrySensitivityError(
                f"{name}: inferred retries {inferred} do not match recorded {dict(recorded)}"
            )

    episodes = []
    for trial_id in sorted(inferred):
        row = by_trial[trial_id]
        episodes.append(
            {
                "classification": row["classification"],
                "failure_classes": sorted(classes[trial_id]),
                "mode": row["mode"],
                "phase": row["phase"],
                "retried_attempts": inferred[trial_id],
                "seed": row["seed"],
                "success": row["success"],
                "trial_id": trial_id,
            }
        )

    counts: dict[str, dict[str, dict[str, int]]] = {}
    for episode in episodes:
        cell = counts.setdefault(episode["phase"], {}).setdefault(episode["mode"], {})
        for failure_class in episode["failure_classes"]:
            cell[failure_class] = cell.get(failure_class, 0) + 1

    arms: dict[str, dict[str, dict[str, int]]] = {}
    for phase in PHASES:
        arms[phase] = {}
        for arm in ARMS:
            rows = [r for r in results if r["phase"] == phase and r["mode"] == arm]
            retried = [e for e in episodes if e["phase"] == phase and e["mode"] == arm]
            retried_successes = sum(1 for e in retried if e["success"])
            successes = sum(1 for r in rows if r["success"])
            arms[phase][arm] = {
                "episodes": len(rows),
                "retried_episodes": len(retried),
                "retried_episodes_that_succeeded": retried_successes,
                "successes_as_recorded": successes,
                "successes_with_retried_episodes_as_failures": successes - retried_successes,
            }
    primary = arms["primary"]
    n = primary["history"]["episodes"]
    if primary["stateless"]["episodes"] != n:
        raise RetrySensitivityError(f"{name}: arms have different primary sizes")

    def difference(key: str) -> dict[str, Any]:
        delta = primary["history"][key] - primary["stateless"][key]
        return {"episodes": delta, "points": round(100 * delta / n, 1)}

    return {
        "execution_plan_digest": plan["execution_plan_digest"],
        "journal_not_retained_disclosure": JOURNAL_NOT_RETAINED.get(name),
        "primary_difference_history_minus_stateless": {
            "as_recorded": difference("successes_as_recorded"),
            "with_retried_episodes_as_failures": difference(
                "successes_with_retried_episodes_as_failures"
            ),
        },
        "results_by_phase_and_arm": arms,
        "retried_attempts_by_phase_arm_and_class": counts,
        "retried_episodes": episodes,
        "retry_rule_in_force": _retry_rule(plan),
        "retry_source": source,
        "total_retried_attempts": sum(e["retried_attempts"] for e in episodes),
        "unresolved_invocations_as_recorded": report.get("unresolved_invocations"),
    }


def analyze(inputs: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    return {
        "campaigns": {
            name: campaign_summary(name, *inputs[name]) for name, _report, _plan in CAMPAIGNS
        },
        "inference_rule": (
            "retries = model_attempts - environment_actions - (1 if the episode ended on "
            "invalid_output or infrastructure_failure else 0); checked against recorded retries "
            "trial by trial wherever they are recorded"
        ),
        "inputs": [path for _name, report, plan in CAMPAIGNS for path in (report, plan)],
        "post_hoc": True,
        "schema_version": SCHEMA_VERSION,
        "scope_note": (
            "Post hoc, written after all outcomes were known. Both columns are reported; neither "
            "replaces the frozen campaign results. Counting a retried episode as a failure is a "
            "pessimistic sensitivity, not a claim that the retry changed the outcome. No test is "
            "recomputed. The historical failure class is not recorded because its journal was not "
            "retained."
        ),
        "written_on": WRITTEN_ON,
    }


def _label(name: str) -> str:
    return name.replace("_", " ")


def render_markdown(result: dict[str, Any]) -> str:
    """Render the Markdown supplement using only values present in ``result``."""

    campaigns = result["campaigns"]
    lines = [
        "# v5 retried attempts and their effect on each arm (post hoc)",
        "",
        (
            "**Post hoc.** Written on "
            f"{result['written_on']}, after all three campaigns' outcomes were known. Generated by "
            "`scripts/generate_v5_retry_sensitivity.py` from "
            + ", ".join(f"`{path}`" for path in result["inputs"])
            + f". Structured source: `{JSON_OUTPUT}`. Verify with "
            "`python -m scripts.verify_v5_retry_sensitivity`. This supplements, and does not "
            "replace, the frozen campaign reports."
        ),
        "",
        "## Retry rule in force",
        "",
        "| Campaign | Transport retry rule | Attempts per action | Retry source |",
        "|---|---|---:|---|",
    ]
    for name, summary in campaigns.items():
        rule = summary["retry_rule_in_force"]
        source = summary["retry_source"]
        if summary["journal_not_retained_disclosure"]:
            source += f"; journal not retained, {summary['journal_not_retained_disclosure']}"
        lines.append(
            f"| {_label(name)} | `{rule['transport_retry_rule']}` | "
            f"{rule['max_model_attempts_per_action']} | {source} |"
        )
    lines += [
        "",
        f"Inference rule: {result['inference_rule']}.",
        "",
        "## Retried attempts by campaign, phase, arm and failure class",
        "",
        "| Campaign | Phase | Arm | Failure class | Retried attempts |",
        "|---|---|---|---|---:|",
    ]
    for name, summary in campaigns.items():
        cells = summary["retried_attempts_by_phase_arm_and_class"]
        for phase in PHASES:
            for arm in ARMS:
                for failure_class, count in sorted(cells.get(phase, {}).get(arm, {}).items()):
                    lines.append(
                        f"| {_label(name)} | {phase} | {arm} | {failure_class} | {count} |"
                    )
        lines.append(
            f"| {_label(name)} | all | all | total | {summary['total_retried_attempts']} |"
        )
    lines += [
        "",
        "## Retried episodes and their final outcome",
        "",
        "| Campaign | Trial | Retried attempts | Final classification | Success |",
        "|---|---|---:|---|---|",
    ]
    for name, summary in campaigns.items():
        for episode in summary["retried_episodes"]:
            lines.append(
                f"| {_label(name)} | `{episode['trial_id']}` | {episode['retried_attempts']} | "
                f"{episode['classification']} | {'yes' if episode['success'] else 'no'} |"
            )
    lines += [
        "",
        "## Primary-phase results, both ways",
        "",
        (
            "| Campaign | Arm | Successes as recorded | Retried episodes (succeeded) | "
            "Successes with retried episodes as failures |"
        ),
        "|---|---|---:|---:|---:|",
    ]
    for name, summary in campaigns.items():
        for arm in ARMS:
            cell = summary["results_by_phase_and_arm"]["primary"][arm]
            n = cell["episodes"]
            lines.append(
                f"| {_label(name)} | {arm} | {cell['successes_as_recorded']}/{n} | "
                f"{cell['retried_episodes']} ({cell['retried_episodes_that_succeeded']}) | "
                f"{cell['successes_with_retried_episodes_as_failures']}/{n} |"
            )
    lines += [
        "",
        "| Campaign | History minus stateless, as recorded | With retried episodes as failures |",
        "|---|---:|---:|",
    ]
    for name, summary in campaigns.items():
        diff = summary["primary_difference_history_minus_stateless"]
        recorded, sensitivity = diff["as_recorded"], diff["with_retried_episodes_as_failures"]
        lines.append(
            f"| {_label(name)} "
            f"| {recorded['episodes']} episodes ({recorded['points']:+.1f} points) "
            f"| {sensitivity['episodes']} episodes ({sensitivity['points']:+.1f} points) |"
        )
    lines += ["", "## Scope", "", result["scope_note"], ""]
    return "\n".join(lines)


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def load_inputs(root: Path) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    def read(path: str) -> dict[str, Any]:
        return json.loads((root / path).read_text(encoding="utf-8"))

    return {name: (read(report), read(plan)) for name, report, plan in CAMPAIGNS}


def rendered_outputs(root: Path) -> dict[str, str]:
    result = analyze(load_inputs(root))
    return {JSON_OUTPUT: serialize(result), MD_OUTPUT: render_markdown(result)}


def verify_outputs(root: Path) -> dict[str, Any]:
    """Regenerate in memory and compare with the committed files; never writes."""

    mismatched = [
        path
        for path, text in rendered_outputs(root).items()
        if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != text
    ]
    if mismatched:
        raise RetrySensitivityError(f"committed outputs differ from regeneration: {mismatched}")
    return {"status": "verified", "outputs": sorted(rendered_outputs(root)), "wrote_files": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    out = args.output_dir or args.repository_root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    for path, text in rendered_outputs(args.repository_root).items():
        (out / Path(path).name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
