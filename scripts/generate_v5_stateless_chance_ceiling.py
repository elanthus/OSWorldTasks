#!/usr/bin/env python3
"""Compare the v5 stateless arm's successes with the chance level its task design implies.

Reads the frozen D5.9 task manifest and the three campaigns' per-episode ``report.json`` files
and writes ``artifacts/grounding-v5-stateless-chance-ceiling-v1.json`` plus a Markdown rendering.
Frozen inputs are not modified. Standard library only; no randomness.

Every v5 task has two deferred choices (the "consumer" stages). Each offers three "Match row ..."
controls, and the reference that decides between them was shown only on an earlier screen. A
stateless policy sees the instruction and the current screenshot, so on those stages it has no
on-screen evidence for the answer. Choices are final and checked only at submission, so an episode
can succeed only if both are right.

Two reference levels are reported, because they answer different questions:

- ``uniform_guess``: an otherwise-perfect policy that picks uniformly at random on each consumer
  stage succeeds with probability 1/9 per episode.
- ``best_fixed_position``: the best policy that always clicks the same on-screen positions on the
  two consumer stages. Answers are not exactly balanced across positions, so this can exceed 1/9.

Neither is a proven upper bound for every conceivable stateless policy; they are reference levels
computed from the frozen manifest.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pixelgym-v5-stateless-chance-ceiling-v1"
MANIFEST_PATH = "artifacts/grounding-v5-d59-freeze/task-manifest.json"
CAMPAIGNS = (
    ("historical", "artifacts/grounding-v5-d59-haiku-results/report.json"),
    ("replication", "artifacts/grounding-v5-d59-replication-results/report.json"),
    ("parser_sensitivity", "artifacts/grounding-v5-d59-action-extraction-results/report.json"),
)
JSON_OUTPUT = "artifacts/grounding-v5-stateless-chance-ceiling-v1.json"
MD_OUTPUT = "artifacts/grounding-v5-stateless-chance-ceiling-v1.md"
CONSUMER_DEPENDENCIES = ("request_ref", "verification_ref")
OPTIONS_PER_CHOICE = 3


class ChanceCeilingError(ValueError):
    """Raised when the frozen inputs do not have the expected structure."""


def consumer_answers(manifest: dict[str, Any]) -> dict[int, tuple[int, int]]:
    """On-screen position (0-2) of the correct control on both consumer stages, per seed."""

    answers: dict[int, tuple[int, int]] = {}
    for record in manifest["records"]:
        seed = record["seed_record"]["seed"]
        positions = []
        for dependency in CONSUMER_DEPENDENCIES:
            stages = [
                stage
                for stage in record["canonical_task"]["stages"]
                if stage.get("critical")
                and stage["kind"] == "click"
                and stage.get("dependency_id") == dependency
            ]
            if len(stages) != 1:
                raise ChanceCeilingError(f"seed {seed}: expected one {dependency} consumer stage")
            stage = stages[0]
            ids = [control["control_id"] for control in stage["controls"]]
            if len(ids) != OPTIONS_PER_CHOICE or stage["target_control_id"] not in ids:
                raise ChanceCeilingError(f"seed {seed}: {dependency} stage is not a 3-way choice")
            if any("no longer visible" not in fact for fact in stage["facts"]):
                raise ChanceCeilingError(f"seed {seed}: {dependency} stage shows extra facts")
            positions.append(ids.index(stage["target_control_id"]))
        if seed in answers:
            raise ChanceCeilingError(f"duplicate seed {seed}")
        answers[seed] = (positions[0], positions[1])
    return answers


def _binomial_cdf(k: int, n: int, p: Fraction) -> float:
    return float(sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1)))


def campaign_summary(
    name: str, path: str, report: dict[str, Any], seeds: set[int]
) -> dict[str, Any]:
    primary = [row for row in report["results"] if row["phase"] == "primary"]
    by_mode: dict[str, list[dict[str, Any]]] = {"history": [], "stateless": []}
    for row in primary:
        by_mode[row["mode"]].append(row)
    for mode, rows in by_mode.items():
        if {row["seed"] for row in rows} != seeds or len(rows) != len(seeds):
            raise ChanceCeilingError(f"{name}/{mode}: primary rows do not cover the manifest seeds")
    stateless = by_mode["stateless"]
    summary: dict[str, Any] = {
        "source": path,
        "episodes_per_arm": len(seeds),
        "history_successes": sum(row["success"] for row in by_mode["history"]),
        "stateless_successes": sum(row["success"] for row in stateless),
        "stateless_classifications": dict(
            sorted(Counter(row["classification"] for row in stateless).items())
        ),
    }
    if all("first_attempts" in row for row in stateless):
        first: dict[int, list[bool]] = {5: [], 7: []}
        both_reached = both_correct = 0
        success_without_both_correct = 0
        for row in stateless:
            attempts = {a["consumer_index"]: a for a in row["first_attempts"]}
            for index, attempt in attempts.items():
                first[index].append(bool(attempt["correct"]))
            reached = set(attempts) == {5, 7}
            correct = reached and all(attempts[i]["correct"] for i in (5, 7))
            both_reached += reached
            both_correct += correct
            success_without_both_correct += bool(row["success"]) and not correct
        summary["first_attempts"] = {
            "consumer_stage_5": {"reached": len(first[5]), "correct": sum(first[5])},
            "consumer_stage_7": {"reached": len(first[7]), "correct": sum(first[7])},
            "both_reached": both_reached,
            "both_first_attempts_correct": both_correct,
            "successes_without_both_first_attempts_correct": success_without_both_correct,
        }
    else:
        summary["first_attempts"] = None
    return summary


def analyze(manifest: dict[str, Any], reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    answers = consumer_answers(manifest)
    n = len(answers)
    seeds = set(answers)
    uniform = Fraction(1, OPTIONS_PER_CHOICE**2)
    joint = Counter(answers.values())
    position_counts = {
        "consumer_stage_5": [sum(a[0] == p for a in answers.values()) for p in range(3)],
        "consumer_stage_7": [sum(a[1] == p for a in answers.values()) for p in range(3)],
    }
    best_position, best_count = max(
        ((pair, joint.get(pair, 0)) for pair in itertools.product(range(3), repeat=2)),
        key=lambda item: (item[1], item[0]),
    )
    campaigns = {}
    for name, path in CAMPAIGNS:
        summary = campaign_summary(name, path, reports[name], seeds)
        observed = summary["stateless_successes"]
        summary["binomial_cdf_at_observed_under_uniform_guess"] = _binomial_cdf(
            observed, n, uniform
        )
        campaigns[name] = summary
    return {
        "schema_version": SCHEMA_VERSION,
        "inputs": [MANIFEST_PATH, *(path for _name, path in CAMPAIGNS)],
        "episodes_per_arm": n,
        "consumer_stage_design": {
            "dependencies": list(CONSUMER_DEPENDENCIES),
            "options_per_choice": OPTIONS_PER_CHOICE,
            "decision_stage_fact": "The source <request|verification> reference is no longer "
            "visible.",
        },
        "uniform_guess": {
            "success_probability": str(uniform),
            "expected_successes": float(uniform * n),
        },
        "answer_positions": position_counts,
        "joint_answer_positions": {f"{a},{b}": joint.get((a, b), 0) for a, b in sorted(joint)},
        "best_fixed_position": {
            "positions": list(best_position),
            "successes_if_otherwise_perfect": best_count,
        },
        "campaigns": campaigns,
        "scope_note": (
            "Reference levels assume a policy that is perfect everywhere except the two consumer "
            "choices. They are not proven upper bounds for every possible stateless policy. The "
            "three campaigns ran on the in-process PIL backend (pixelgym/grounding/v5/backend.py "
            "and its memory-focus subclass), not a browser or OSWorld."
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render the Markdown supplement using only values present in ``result``."""

    n = result["episodes_per_arm"]
    uniform, best = result["uniform_guess"], result["best_fixed_position"]
    lines = [
        "# v5 stateless arm against its chance level",
        "",
        (
            "Generated by `scripts/generate_v5_stateless_chance_ceiling.py` from "
            + ", ".join(f"`{path}`" for path in result["inputs"])
            + ". Structured source: `artifacts/grounding-v5-stateless-chance-ceiling-v1.json`. "
            "This supplements, and does not replace, the frozen campaign reports."
        ),
        "",
        "## Why there is a chance level",
        "",
        (
            "Each task has two deferred choices with "
            f"{result['consumer_stage_design']['options_per_choice']} options. The reference that "
            "decides each one appeared only on an earlier screen; the decision screen says "
            f'"{result["consumer_stage_design"]["decision_stage_fact"]}" A stateless policy '
            "sees only the instruction and the current screenshot, and both choices must be right "
            "for the submission to succeed."
        ),
        "",
        (
            f"- Uniform guess, otherwise perfect: probability {uniform['success_probability']}, "
            f"{uniform['expected_successes']:.1f} of {n} episodes expected."
        ),
        (
            "- Best fixed on-screen positions "
            f"({best['positions'][0]}, {best['positions'][1]}), otherwise perfect: "
            f"{best['successes_if_otherwise_perfect']} of {n}."
        ),
        "",
        "## Observed",
        "",
        (
            "| Campaign | Stateful successes | Stateless successes | P(X ≤ observed), "
            "Binomial(n, 1/9) |"
        ),
        "|---|---:|---:|---:|",
    ]
    for name, summary in result["campaigns"].items():
        lines.append(
            f"| {name.replace('_', ' ')} | {summary['history_successes']}/{n} | "
            f"{summary['stateless_successes']}/{n} | "
            f"{summary['binomial_cdf_at_observed_under_uniform_guess']:.3f} |"
        )
    lines += ["", "## First attempts on the two choices (stateless arm)", ""]
    lines += [
        (
            "| Campaign | Stage 5 correct / reached | Stage 7 correct / reached | Both reached | "
            "Both correct | Successes without both correct |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, summary in result["campaigns"].items():
        first = summary["first_attempts"]
        if first is None:
            lines.append(f"| {name.replace('_', ' ')} | not recorded | | | | |")
            continue
        s5, s7 = first["consumer_stage_5"], first["consumer_stage_7"]
        lines.append(
            f"| {name.replace('_', ' ')} | {s5['correct']}/{s5['reached']} | "
            f"{s7['correct']}/{s7['reached']} | {first['both_reached']} | "
            f"{first['both_first_attempts_correct']} | "
            f"{first['successes_without_both_first_attempts_correct']} |"
        )
    positions = result["answer_positions"]
    lines += [
        "",
        (
            "Correct on-screen position counts (0, 1, 2): stage 5 "
            f"{positions['consumer_stage_5']}, stage 7 {positions['consumer_stage_7']}."
        ),
        "",
        "## Scope",
        "",
        result["scope_note"],
        "",
    ]
    return "\n".join(lines)


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def load_inputs(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    reports = {
        name: json.loads((root / path).read_text(encoding="utf-8")) for name, path in CAMPAIGNS
    }
    return manifest, reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    result = analyze(*load_inputs(args.repository_root))
    out = args.output_dir or args.repository_root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    (out / Path(JSON_OUTPUT).name).write_text(serialize(result), encoding="utf-8")
    (out / Path(MD_OUTPUT).name).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
