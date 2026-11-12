#!/usr/bin/env python3
"""Render the behaviour-cloning experiment report from stored results, without re-running.

Reads ``artifacts/bc-experiment/results.json`` (written by ``scripts/run_bc_experiment.py``) and
writes ``artifacts/bc-experiment/REPORT.md`` and ``artifacts/bc-experiment/curve.svg``.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pixelgym-bc-experiment-v1"
DEFAULT_INPUT = Path("artifacts/bc-experiment/results.json")
METRICS = (
    ("action_type_accuracy", "Action type", "#1f77b4"),
    ("click_target_accuracy", "Click target", "#2ca02c"),
    ("key_accuracy", "Key", "#d62728"),
    ("step_accuracy", "Whole step", "#111111"),
)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _by_demos(results: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in results["runs"]:
        grouped[run["demo_episodes"]].append(run)
    return dict(sorted(grouped.items()))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def render_svg(results: dict[str, Any]) -> str:
    """Teacher-forced test accuracy (mean over training seeds) against demo episodes, log2 x."""

    grouped = _by_demos(results)
    counts = list(grouped)
    width, height, left, right, top, bottom = 640, 380, 64, 160, 24, 52
    plot_w, plot_h = width - left - right, height - top - bottom
    low, high = math.log2(counts[0]), math.log2(counts[-1])
    span = high - low or 1.0

    def x_of(count: int) -> float:
        return left + plot_w * (math.log2(count) - low) / span

    def y_of(value: float) -> float:
        return top + plot_h * (1 - value)

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">'
        ),
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y_of(tick)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#dddddd"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{int(tick * 100)}%</text>'
        )
    for count in counts:
        x = x_of(count)
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 18}" text-anchor="middle">{count}</text>'
        )
    parts.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{height - 12}" text-anchor="middle">'
        "Demonstration episodes (log scale)</text>"
    )
    parts.append(
        f'<text x="16" y="{top + plot_h / 2:.1f}" text-anchor="middle" '
        f'transform="rotate(-90 16 {top + plot_h / 2:.1f})">Held-out teacher-forced accuracy</text>'
    )
    for index, (key, label, colour) in enumerate(METRICS):
        points = []
        for count, runs in grouped.items():
            values = [run["teacher_forced"][key] for run in runs]
            if any(value is None for value in values):
                continue
            points.append((x_of(count), y_of(_mean(values))))
            for value in values:  # individual training seeds as small hollow marks
                parts.append(
                    f'<circle cx="{x_of(count):.1f}" cy="{y_of(value):.1f}" r="2.5" '
                    f'fill="none" stroke="{colour}"/>'
                )
        if points:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            parts.append(
                f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2"/>'
            )
        legend_y = top + 16 + 20 * index
        parts.append(
            f'<line x1="{left + plot_w + 16}" y1="{legend_y - 4}" x2="{left + plot_w + 36}" '
            f'y2="{legend_y - 4}" stroke="{colour}" stroke-width="2"/>'
        )
        parts.append(f'<text x="{left + plot_w + 42}" y="{legend_y}">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render(results: dict[str, Any]) -> str:
    if results.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected {SCHEMA_VERSION}, got {results.get('schema_version')!r}")
    design, data, host = results["design"], results["data"], results["host"]
    grouped = _by_demos(results)
    lines = [
        "# Behaviour cloning from the scripted oracle",
        "",
        (
            f"Generated from stored results `results.json`, run `{results['generated_at']}` at "
            f"revision `{results['git_revision']}` (worktree clean: "
            f"{'yes' if results['worktree_clean'] else 'no'}); "
            f"{results['total_seconds'] / 60:.0f} min wall clock on {host['system']} "
            f"{host['machine']}, torch {host['torch']}."
        ),
        "",
        results["scope_note"],
        "",
        "## Design",
        "",
        f"- Oracle: `{design['oracle']}` (privileged at data generation only).",
        f"- Observation: {design['observation']}.",
        (
            f"- Seeds: demonstrations from {design['train_pool_seeds'][0]}–"
            f"{design['train_pool_seeds'][1]} (nested subsets), validation "
            f"{design['validation_seeds'][0]}–{design['validation_seeds'][1]} for checkpoint "
            f"selection, test {design['test_seeds'][0]}–{design['test_seeds'][1]} for every number "
            "below."
        ),
        (
            f"- Training: {design['steps']} steps, batch {design['batch_size']}, "
            f"{design['optimizer']}; {design['checkpoint_selection']}, evaluated every "
            f"{design['eval_every']} steps."
        ),
        (
            f"- Click head: {design['click_grid'][0]}x{design['click_grid'][1]} cells of "
            f"{design['click_cell_pixels']} px; a click counts as correct when it lands on the "
            "same widget (or payment option) as the oracle's click."
        ),
        (
            f"- Closed loop: up to {design['max_episode_steps']} steps per test episode; success "
            "is termination with reward 1.0 from the privileged evaluator."
        ),
        (
            f"- Data: {data['pool_frames']} pool frames, {data['validation_frames']} validation, "
            f"{data['test_frames']} test."
        ),
        "",
        "## Held-out results (mean over training seeds)",
        "",
        (
            "Teacher-forced accuracy is measured on the oracle's own test states; closed-loop "
            "numbers come from the policy acting alone."
        ),
        "",
        (
            "| Demos | Frames | Action type | Click target | Key | Whole step | Episode success | "
            "Fields correct (of 7) |"
        ),
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for count, runs in grouped.items():
        teacher = [run["teacher_forced"] for run in runs]
        closed = [run["closed_loop"] for run in runs]
        cells = []
        for key, _label, _colour in METRICS:
            values = [row[key] for row in teacher]
            cells.append(_pct(None if None in values else _mean(values)))
        successes = sum(row["successes"] for row in closed)
        episodes = sum(row["episodes"] for row in closed)
        fields = _mean([row["mean_fields_correct"] for row in closed])
        lines.append(
            f"| {count} | {runs[0]['demo_frames']} | {' | '.join(cells)} | "
            f"{successes}/{episodes} | {fields:.2f} |"
        )
    lines.extend(
        [
            "",
            "![Held-out teacher-forced accuracy against demonstrations](curve.svg)",
            "",
            "## Every run",
            "",
            (
                "| Demos | Training seed | Best validation step | Validation step acc. | "
                "Test step acc. | Test click acc. | Test key acc. | Successes | Mean steps | "
                "Submissions per episode | Train time |"
            ),
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for run in results["runs"]:
        closed = run["closed_loop"]
        submissions = sorted({episode["submissions"] for episode in closed["per_episode"]})
        lines.append(
            f"| {run['demo_episodes']} | {run['training_seed']} | {run['best_validation_step']} "
            f"| {_pct(run['best_validation_step_accuracy'])} "
            f"| {_pct(run['teacher_forced']['step_accuracy'])} "
            f"| {_pct(run['teacher_forced']['click_target_accuracy'])} "
            f"| {_pct(run['teacher_forced']['key_accuracy'])} "
            f"| {closed['successes']}/{closed['episodes']} | {closed['mean_steps']:.0f} "
            f"| {', '.join(str(value) for value in submissions) or '—'} "
            f"| {run['train_seconds'] / 60:.1f} min |"
        )
    final_step = results["design"]["steps"]
    at_final = sum(run["best_validation_step"] == final_step for run in results["runs"])
    first = results["runs"][0]
    lines.extend(
        [
            "",
            f"Model: {first['parameters']:,} parameters on `{first['device']}`.",
            "",
            (
                "Submissions per episode lists the distinct counts seen across the test episodes "
                "of a run. Fields correct is scored after the episode ends; "
                "`tests/unit/test_bc_experiment.py` checks it against the score of the untouched "
                "form on the same test seeds."
            ),
            "",
            "## Limits",
            "",
            (
                f"- {at_final} of {len(results['runs'])} runs selected the final checkpoint "
                f"(step {final_step}), so validation accuracy was still rising when training "
                "stopped."
            ),
            (
                "- One oracle that never errs: the policy sees no recovery states, so closed-loop "
                "errors compound (no DAgger)."
            ),
            (
                "- An episode needs about 110 consecutive correct actions; per-step accuracy p "
                "gives roughly p^110 episode success, so small per-step gaps erase episode success."
            ),
            "- Fixed hyperparameters, two training seeds per point, one host; no search was run.",
            "- In-process fake backend only; not evidence about the browser or OSWorld path.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args(argv)
    results = json.loads(args.input.read_text(encoding="utf-8"))
    report = args.input.with_name("REPORT.md")
    report.write_text(render(results), encoding="utf-8")
    args.input.with_name("curve.svg").write_text(render_svg(results), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
