#!/usr/bin/env python3
"""Render Markdown from stored fake-backend throughput evidence without re-measuring.

Reads ``artifacts/throughput/fake-throughput.json`` (written by
``scripts/measure_fake_throughput.py``) and writes ``artifacts/throughput/REPORT.md``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_INPUT = Path("artifacts/throughput/fake-throughput.json")
DEFAULT_OUTPUT = Path("artifacts/throughput/REPORT.md")
SCHEMA_VERSION = "pixelgym-fake-throughput-v1"


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def render(evidence: dict[str, Any]) -> str:
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected {SCHEMA_VERSION}, got {evidence.get('schema_version')!r}")
    host = evidence["host"]
    parameters = evidence["parameters"]
    lines = [
        "# Fake-backend throughput",
        "",
        (
            "Generated from stored evidence `fake-throughput.json`, measured "
            f"`{evidence['generated_at']}` at revision `{evidence['git_revision']}` "
            f"(worktree clean: {'yes' if evidence['worktree_clean'] else 'no'})."
        ),
        "",
        (
            f"Host: {host['cpu']}, {host['system']} {host['release']} ({host['machine']}); "
            f"Python {host['python']}, NumPy {host['numpy']}, Pillow {host['pillow']}, "
            f"Gymnasium {host['gymnasium']}."
        ),
        "",
        evidence["scope_note"],
        "",
        (
            f"Environment `{evidence['environment']['id']}`, observation "
            f"{'x'.join(str(n) for n in evidence['environment']['observation_shape'])} "
            f"{evidence['environment']['observation_dtype']}. Command: `{evidence['command']}`."
        ),
        "",
        "## Steps per second (single environment)",
        "",
        f"{parameters['repeats']} repeats of {parameters['steps']} steps each; median (min–max).",
        "",
    ]
    # Evidence written after the caret change also stores the dispatched action types, so the
    # NOOP share is read from the evidence; older files only carry the sampling-design label.
    with_types = all("action_types" in evidence["workloads"][w] for w in ("random", "golden"))
    if with_types:
        lines.extend(
            [
                (
                    "| Workload | NOOP share | Static-layer renderer | Single-pass reference "
                    "| Speed-up (medians) |"
                ),
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        descriptions = {
            "random": "Random policy (NOOPs reuse the cached frame)",
            "golden": "Golden trajectory replay",
        }
    else:
        lines.extend(
            [
                "| Workload | Static-layer renderer | Single-pass reference | Speed-up (medians) |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        descriptions = {
            "random": "Random policy (a third are NOOPs, which reuse the cached frame)",
            "golden": "Golden trajectory replay (renders almost every step)",
        }
    for workload, label in descriptions.items():
        row = evidence["workloads"][workload]
        cells = []
        for renderer in ("static_layer", "reference_single_pass"):
            summary = row[renderer]["steps_per_second"]
            cells.append(f"{summary['median']:.0f} ({summary['min']:.0f}–{summary['max']:.0f})")
        if with_types:
            types = row["action_types"]
            share = f"{types['noop_share']:.1%} ({types['counts']['NOOP']}/{types['steps']})"
            lines.append(
                f"| {label} | {share} | {cells[0]} | {cells[1]} | {row['speedup_median']:.2f}x |"
            )
        else:
            lines.append(f"| {label} | {cells[0]} | {cells[1]} | {row['speedup_median']:.2f}x |")

    lines.extend(
        [
            "",
            "## Reset latency",
            "",
            (
                f"{parameters['resets']} resets over distinct seeds per repeat; values are the "
                "median across repeats of each statistic."
            ),
            "",
            "| Renderer | Median | p95 | Max |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for renderer, label in (
        ("static_layer", "Static-layer renderer"),
        ("reference_single_pass", "Single-pass reference"),
    ):
        runs = evidence["reset_latency"][renderer]
        stats = [_median([run[key] for run in runs]) for key in ("median_ms", "p95_ms", "max_ms")]
        lines.append(f"| {label} | {stats[0]:.2f} ms | {stats[1]:.2f} ms | {stats[2]:.2f} ms |")

    lines.extend(
        [
            "",
            "## Vectorized (random policy, static-layer renderer)",
            "",
            "| Vector env | Sub-environments | Env steps/s, median (min–max) |",
            "| --- | ---: | ---: |",
        ]
    )
    for kind, label in (("sync", "`SyncVectorEnv`"), ("async", "`AsyncVectorEnv` (spawn)")):
        runs = evidence["vector"][kind]
        rates = [run["env_steps_per_second"] for run in runs]
        lines.append(
            f"| {label} | {runs[0]['num_envs']} | "
            f"{_median(rates):.0f} ({min(rates):.0f}–{max(rates):.0f}) |"
        )

    lines.extend(
        [
            "",
            "## Byte identity of the faster renderer",
            "",
            (
                "An untimed pass replays the same seeded action streams through both renderers "
                "and compares the SHA-256 of every frame, including each reset frame."
            ),
            "",
            "| Workload | Frames compared | Mismatched frames |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in evidence["byte_identity"]:
        lines.append(
            f"| {row['workload']} | {row['frames_compared']} | {row['mismatched_frames']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    evidence = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(render(evidence), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
