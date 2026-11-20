#!/usr/bin/env python3
"""Render Markdown from stored fake-versus-Chromium differential evidence without rerunning it.

Reads the JSON written by ``scripts/run_browser_differential_evidence.py`` and writes a report
beside it. Every number and verdict-free statement comes from the evidence file; the fixed text
only names what each table holds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pixelgym-browser-differential-evidence-v1"


def _seed_label(run: dict[str, Any]) -> str:
    if run["derandomize"]:
        return "none (`derandomize=True`)"
    return f"`{run['hypothesis_seed']}`"


def render(evidence: dict[str, Any], *, source_name: str) -> str:
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected {SCHEMA_VERSION}, got {evidence.get('schema_version')!r}")
    host = evidence["platform"]
    lines = [
        "# Fake versus Chromium: differential comparison and barrier controls",
        "",
        (
            f"Generated from stored evidence `{source_name}`, recorded "
            f"`{evidence['generated_at']}` at revision `{evidence['git_revision']}` "
            f"(worktree clean: {'yes' if evidence['worktree_clean'] else 'no'}) by "
            f"`{evidence['generator']}`."
        ),
        "",
        (
            f"Platform: {host['system']} {host['release']} ({host['machine']}); Python "
            f"{host['python']}; Chromium {host['chromium']} via Playwright {host['playwright']}; "
            f"Hypothesis {host['hypothesis']}. Input contract `{evidence['input_contract']}`, fake "
            f"renderer `{evidence['fake_renderer']}`. Task seeds sampled from "
            f"{', '.join(str(s) for s in evidence['task_seeds'])}."
        ),
        "",
        "## Differential runs",
        "",
        (
            "Each row is one Hypothesis run without shrinking. A divergence is the first action "
            "after which the fake's form state or submissions differ from Chromium's."
        ),
        "",
        "| Run | Hypothesis seed | Sequences | Actions | Divergent sequences | Seconds |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for run in evidence["runs"]:
        lines.append(
            f"| {run['label']} | {_seed_label(run)} | {run['sequence_count']} | "
            f"{run['action_count']} | {run['divergence_count']} | {run['seconds']} |"
        )
    divergent = [
        (run["label"], record)
        for run in evidence["runs"]
        for record in run["sequences"]
        if record["divergence"] is not None
    ]
    lines.extend(["", "## Divergences", ""])
    if divergent:
        lines.extend(
            [
                "| Run | Task seed | Action index | Kind | Action |",
                "| --- | ---: | ---: | --- | --- |",
            ]
        )
        for label, record in divergent:
            divergence = record["divergence"]
            index = divergence["index"]
            action = "" if index is None else f"`{record['actions'][index]}`"
            lines.append(
                f"| {label} | {record['task_seed']} | {index} | {divergence['kind']} | {action} |"
            )
    else:
        lines.append(f"Divergent sequences across all runs: {len(divergent)}.")

    barrier = evidence["barrier"]
    negative = barrier["negative_control_barrier_off"]
    positive = barrier["positive_control_barrier_on"]
    lines.extend(
        [
            "",
            "## Acknowledgement barrier",
            "",
            (
                f"Source: `{barrier['source']}`. One click on Submit with the submission held at "
                "the server; the value is the number of submissions each in-step evaluator read "
                "returned."
            ),
            "",
            "| Control | Barrier | In-step reads | Timeline (reward, terminated, truncated) |",
            "| --- | --- | --- | --- |",
            (
                f"| Negative (held until after the read) | off | {negative['in_step_reads']} | "
                f"{negative['timeline']} |"
            ),
            (
                f"| Positive (released after arrival) | on | {positive['in_step_reads']} | "
                f"{positive['timeline']} |"
            ),
            "",
            f"Golden trajectory `{barrier['golden_fixture']}` under submit latency:",
            "",
            (
                "| Submit delay (s) | Steps | Positive-reward steps | Terminated steps | "
                "Truncated steps | Last in-step read | Observation equals later frame |"
            ),
            "| ---: | ---: | --- | --- | --- | ---: | --- |",
        ]
    )
    for run in barrier["golden_under_submit_latency"]:
        lines.append(
            f"| {run['submit_delay_seconds']} | {run['steps']} | {run['positive_reward_steps']} | "
            f"{run['terminated_steps']} | {run['truncated_steps']} | {run['in_step_reads_last']} | "
            f"{run['observation_equals_later_frame']} |"
        )
    lines.extend(["", f"Total run time: {evidence['total_seconds']} s.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    evidence = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(render(evidence, source_name=args.input.name), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
