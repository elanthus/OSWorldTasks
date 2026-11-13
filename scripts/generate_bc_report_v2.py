#!/usr/bin/env python3
"""Render the behaviour-cloning v2 report from stored results, without re-running anything.

Reads a ``results.json`` written by ``scripts/run_bc_experiment_v2.py`` (phase ``small`` or
``grid``) and writes ``REPORT.md`` beside it. Every number comes from that file. The Limits
section holds only sentences computed from it or plain statements of the stored design.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.tasks.vendor_form import generator

SCHEMA_VERSION = "pixelgym-bc-experiment-v2"
DEFAULT_INPUT = Path("artifacts/bc-experiment-v2/small/results.json")
METRICS = (
    ("action_type_accuracy", "Action type"),
    ("click_target_accuracy", "Click target"),
    ("key_accuracy", "Key"),
    ("step_accuracy", "Whole step"),
)
BASELINES = ("always_key", "constant_click", "card_blind", "lookup_table", "oracle")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _loop(row: dict[str, Any]) -> str:
    loop = row.get("closed_loop")
    if not loop:
        return "—"
    return f"{loop['successes']}/{loop['episodes']}"


def _fields(row: dict[str, Any]) -> str:
    loop = row.get("closed_loop") or {}
    if "mean_fields_correct" not in loop:
        return "—"
    return f"{loop['mean_fields_correct']:.2f}/{loop['fields_total']}"


def _metric_row(label: str, row: dict[str, Any]) -> str:
    tf = row["teacher_forced"]
    cells = [_pct(tf[key]) for key, _ in METRICS]
    return f"| {label} | " + " | ".join(cells) + f" | {_loop(row)} | {_fields(row)} |"


def _component_overlap(results: dict[str, Any]) -> tuple[int, int, int]:
    """Held-out names whose prefix, and whose suffix, also occur in a training name."""

    held = set(results["held_out_names"])
    prefixes, suffixes = generator._COMPANY_PREFIXES, generator._COMPANY_SUFFIXES
    split = {f"{p} {s}": (p, s) for p in prefixes for s in suffixes}
    training = [split[name] for name in split if name not in held]
    train_prefixes = {p for p, _ in training}
    train_suffixes = {s for _, s in training}
    prefix_seen = sum(split[name][0] in train_prefixes for name in held)
    suffix_seen = sum(split[name][1] in train_suffixes for name in held)
    return len(held), prefix_seen, suffix_seen


def render(results: dict[str, Any]) -> str:
    if results.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected {SCHEMA_VERSION}, got {results.get('schema_version')!r}")
    config, host, gates = results["config"], results["host"], results["gates"]
    lines = [
        f"# Behaviour cloning v2 ({results['phase']} phase)",
        "",
        (
            f"Generated from stored results `results.json`, recorded `{results['generated_at']}` "
            f"at revision `{results['git_revision']}` (worktree clean: "
            f"{_yes(results['worktree_clean'])}) by `{results['command']}`."
        ),
        "",
        (
            f"Host: {host['system']} {host['release']} ({host['machine']}); Python "
            f"{host['python']}, NumPy {host['numpy']}, torch {host['torch']}; device "
            f"`{host['device']}`. Total time {results['total_seconds'] / 60:.1f} min."
        ),
        "",
        "## Sanity gates",
        "",
        "| Gate | Result | Threshold | Passed |",
        "| --- | --- | --- | --- |",
    ]
    overfit = gates["overfit_30_click_frames"]
    lines.append(
        f"| Overfit {overfit['frames']} training click frames "
        f"({overfit['training']['steps']} steps) | "
        f"{overfit['click_targets_correct']}/{overfit['frames']} click targets correct | "
        f"≥ {overfit['min_correct']} | {_yes(overfit['passed'])} |"
    )
    single = gates["single_demo_closed_loop"]
    episode = single["closed_loop"]
    lines.append(
        f"| One demonstration (seed {single['seed']}, {single['frames']} frames, "
        f"{single['training']['steps']} steps), replayed closed-loop on its own seed | "
        f"success: {_yes(episode['success'])}; {episode['fields_correct']}/7 fields, "
        f"{episode['steps']} steps | success | {_yes(single['passed'])} |"
    )
    for split, lookup in gates["lookup_table_test_seeds"].items():
        lines.append(
            f"| Frame-to-action lookup table on the `{split}` test seeds | "
            f"{lookup['successes']}/{lookup['episodes']} episodes; "
            f"{lookup['conflicting_frames']} conflicting frames | all episodes | "
            f"{_yes(lookup['passed'])} |"
        )
    lines += ["", f"All gates passed: {_yes(gates['all_passed'])}.", ""]

    for split, data in results["data"].items():
        overlap = data["name_overlap"]
        lines += [
            f"## Split `{split}`",
            "",
            (
                f"Training pool {len(data['train_seeds'])} episodes ({data['pool_frames']} "
                f"frames), validation {len(data['validation_seeds'])} episodes "
                f"({data['validation_frames']} frames), test {len(data['test_seeds'])} "
                f"episodes ({data['test_frames']} frames). The test episodes use "
                f"{overlap['test_distinct_names']} distinct company names, "
                f"{overlap['test_distinct_names_in_train']} of which occur in the training pool."
            ),
            "",
            (
                "Teacher-forced metrics are on the test frames (the oracle's own states); "
                "closed-loop columns are test episodes. Baselines are defined below the table."
            ),
            "",
            "| Policy | " + " | ".join(label for _, label in METRICS) + " | Successes | Fields |",
            "| --- | " + " | ".join("---:" for _ in METRICS) + " | ---: | ---: |",
        ]
        for run in (r for r in results["runs"] if r["split"] == split):
            label = f"BC, {run['demo_episodes']} demos, training seed {run['training_seed']}"
            lines.append(_metric_row(label, run))
        split_baselines = results["baselines"][split]
        for name in BASELINES:
            lines.append(_metric_row(f"Baseline: {name.replace('_', ' ')}", split_baselines[name]))
        lines += [""]
        for name in BASELINES:
            lines.append(f"- **{name.replace('_', ' ')}**: {split_baselines[name]['definition']}.")
        lines += [""]

    runs = results["runs"]
    if runs:
        lines += [
            "## Runs",
            "",
            (
                "| Run | Frames | Steps | Best validation step | Validation step acc. | "
                "Train time | Weights SHA-256 | Weights bytes |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
        for run in runs:
            lines.append(
                f"| `{run['name']}` | {run['demo_frames']} | {run['steps']} | "
                f"{run['best_validation_step']} | {_pct(run['best_validation_step_accuracy'])} | "
                f"{run['train_seconds'] / 60:.1f} min | `{run['weights']['sha256'][:16]}…` | "
                f"{run['weights']['bytes']} |"
            )
        lines += [
            "",
            (
                "Per-frame test predictions are stored under `predictions/`, with the test "
                "frames' oracle actions in `test-frames-<split>.json`. Weights are not checked in."
            ),
            "",
        ]

    lines += ["## Limits", ""]
    lines.append(
        "- Design: the policy sees one grayscale "
        f"{config['observation_height']}x{config['observation_width']} frame per step, with no "
        "memory, and is trained only on the oracle's own states (no recovery data)."
    )
    lines.append(
        f"- Design: checkpoints are selected on validation seeds every {config['eval_every']} "
        "steps; there was no hyperparameter search, and test seeds were used only for the "
        "reported numbers."
    )
    lines.append(
        f"- Design: clicks are predicted on a {1024 // config['cell_pixels']}x"
        f"{768 // config['cell_pixels']} grid of {config['cell_pixels']}-pixel cells and "
        "executed at the cell centre."
    )
    standard = results["data"]["standard"]["name_overlap"]
    lines.append(
        f"- The generator has {standard['distinct_names_total']} company names. In the standard "
        f"split, {standard['test_episodes_with_name_in_train']} of {standard['test_episodes']} "
        "test episodes use a name that also occurs in the training pool."
    )
    if "name_holdout" in results["data"]:
        held, prefix_seen, suffix_seen = _component_overlap(results)
        holdout = results["data"]["name_holdout"]["name_overlap"]
        lines.append(
            f"- In the name-holdout split, {holdout['test_episodes_with_name_in_train']} of "
            f"{holdout['test_episodes']} test episodes use a training name. Of the {held} held-out "
            f"names, {prefix_seen} share their prefix and {suffix_seen} their suffix with a "
            "training name."
        )
    else:
        lines.append("- This phase did not run the name-holdout split.")
    if runs:
        last = sum(
            run["best_validation_step"] == run["validation_curve"][-1]["step"] for run in runs
        )
        lines.append(
            f"- In {last} of {len(runs)} runs the selected checkpoint was the last validation "
            "evaluation."
        )
        lines.append(
            f"- Each configuration ran with {len({r['training_seed'] for r in runs})} training "
            f"seed(s) on one host (`{host['device']}`)."
        )
    lines.append(
        "- The card-blind baseline uses privileged form state and has no closed-loop rollout."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    results = json.loads(args.input.read_text(encoding="utf-8"))
    output = args.output or args.input.with_name("REPORT.md")
    output.write_text(render(results), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
