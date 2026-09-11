"""Generate response-free development admission and the non-executable D5.8 pilot plan."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from pixelgym.grounding.v5.admission import replay_actions, validate_task_admission
from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.memory_generator import (
    COUNTERFACTUAL_SEEDS,
    MEMORY_GENERATOR_VERSION,
    development_counterfactuals,
    generate_memory_task,
)
from pixelgym.grounding.v5.memory_plan import pilot_plan
from pixelgym.grounding.v5.policies import _append_golden_stage, click_action, noop_action

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/grounding-v5-d58-design/memory-repair"
SOURCE_PATHS = (
    "pixelgym/actions.py",
    "pixelgym/env.py",
    "pixelgym/evaluator.py",
    "pixelgym/grounding/v5/contracts.py",
    "pixelgym/grounding/v5/seeds.py",
    "pixelgym/grounding/v5/generator.py",
    "pixelgym/grounding/v5/backend.py",
    "pixelgym/grounding/v5/admission.py",
    "pixelgym/grounding/v5/policies.py",
    "pixelgym/grounding/v5/memory_generator.py",
    "pixelgym/grounding/v5/memory_backend.py",
    "pixelgym/grounding/v5/screenshot_memory.py",
    "pixelgym/grounding/v5/memory_plan.py",
    "pixelgym/grounding/v5/schemas/memory-task.schema.json",
    "pixelgym/grounding/v5/runner.py",
    "pixelgym/grounding/v5/panel_policy.py",
    "pixelgym/grounding/v5/openrouter_policy.py",
    "pixelgym/grounding/v5/journal.py",
    "pixelgym/grounding/v5/resume.py",
    "pixelgym/grounding/v5/coordinates.py",
    "scripts/prepare_grounding_v5_memory.py",
    "pyproject.toml",
    "requirements/platform-py312.lock",
)


def source_digests() -> dict[str, str]:
    return {name: "sha256:" + sha256_bytes((ROOT / name).read_bytes()) for name in SOURCE_PATHS}


def frame_at(seed: int, stage_index: int) -> NDArray[np.uint8]:
    backend = MemoryBackend()
    try:
        backend.reset(seed)
        actions: list[dict[str, int]] = []
        for stage in backend.task.stages[:stage_index]:
            _append_golden_stage(backend, stage, actions)
        return backend.screenshot().copy()
    finally:
        backend.close()


def pixel_difference(first: NDArray[np.uint8], second: NDArray[np.uint8]) -> dict[str, int]:
    delta = np.abs(first.astype(np.int16) - second.astype(np.int16))
    return {
        "differing_pixel_count": int(np.any(delta != 0, axis=2).sum()),
        "max_per_channel_delta": int(delta.max()),
    }


def baseline(seed: int, rule: str) -> dict[str, Any]:
    """Privileged scripted competence outside consumers; current-choice-only at consumers."""
    backend = MemoryBackend()
    task = generate_memory_task(seed)
    actions: list[dict[str, int]] = []
    choices = []
    try:
        backend.reset(seed)
        for index, stage in enumerate(task.stages):
            if index not in (5, 7):
                _append_golden_stage(backend, stage, actions)
                continue
            controls = backend.visible_controls()
            if rule.startswith("position-"):
                selected = controls[int(rule[-1])]
            else:
                ordered = sorted(controls, key=lambda control: control.label)
                selected = ordered[0 if rule == "label-min" else -1]
            choices.append(
                {
                    "consumer_index": index,
                    "selected_control": selected.control_id,
                    "first_attempt_correct": selected.control_id == stage.target_control_id,
                }
            )
            actions.append(click_action(*selected.center))
            backend.click(*selected.center)
            if stage.recovery_stage:
                center = backend.control_center("repair_implicated")
                actions.append(click_action(*center))
                backend.click(*center)
    finally:
        backend.close()
    actions.extend(noop_action() for _ in range(task.max_episode_steps - len(actions)))
    result = replay_actions(task, tuple(actions), backend_factory=MemoryBackend)
    return {
        "seed": seed,
        "choices": choices,
        "success": result.success,
        "reward_sum": sum(result.rewards),
        "trace_digest": content_digest(actions),
    }


def render_report(value: dict[str, Any]) -> str:
    """Render only stored evidence; never rerun tasks or reinterpret failures."""
    summary = value["summary"]
    lines = [
        "# D5.8 memory repair — development evidence",
        "",
        f"Generator: `{value['generator_version']}`. Provider calls: **0**. New spend: **USD 0**.",
        "",
        (
            f"{summary['admitted_task_count']} development tasks completed deterministic golden and recovery replays, "
            "six mutation replays each, and the random-action floor check. "
            "No confirmatory task was generated or evaluated."
        ),
        "",
        (
            f"All {summary['counterfactual_pair_count']} source interventions changed the required answer "
            "while retaining exactly identical consumer pixels and task instruction. "
            "Every source image changed. Consumer differing-pixel count and maximum channel delta "
            "are both zero, with no masking or tolerance."
        ),
        "",
        (
            "Correct memory-choice positions in the 24 base development tasks (48 choices): "
            f"`{json.dumps(summary['memory_target_positions'], sort_keys=True)}`. "
            "The layout uses a target-independent deterministic permutation; finite samples need not balance exactly."
        ),
        "",
        "## Scripted construct diagnostics",
        "",
        (
            "These traces use privileged golden actions outside the two memory consumers. "
            "They are not pixel-only model scores. Each consumer uses only a fixed position or "
            "lexical ordering of the current labels. No retry or correctness feedback is available."
        ),
        "",
        "| Consumer rule | First choices correct / 48 | Terminal successes / 24 |",
        "|---|---:|---:|",
    ]
    for rule, row in summary["baselines"].items():
        lines.append(f"| {rule} | {row['first_attempt_correct']} | {row['successes']} |")
    lines += [
        "",
        (
            "The counterfactual pairs establish that the current image cannot uniquely determine "
            "the answer. They do not establish model ability, the size of a memory benefit, or "
            "confirmatory power. Human usability review and fresh end-to-end calibration remain open."
        ),
        "",
        "## Review artifacts",
        "",
        (
            "[Stored evidence](admission.json), [source binding](sources.json), "
            "[exact diagnostic call plan](pilot-plan.json). The call plan is non-executable, "
            "requires exact execution approval, and has zero confirmatory calls."
        ),
        "",
        (
            "The approved USD 5 ceiling is shared across all successor phases. The conservative "
            "full-context reservation does not guarantee completion of twenty requests under that ceiling. "
            "An execution driver enforcing the shared durable ledger is required before any call."
        ),
        "",
        (
            "The successor currently uses the deterministic in-process pixel renderer. "
            "No browser/OSWorld integration or human usability verdict is claimed."
        ),
        "",
        (
            "Source images: [base](base-source.png), [changed fact](counterfactual-source.png). "
            "Consumer images: [base](base-consumer.png), [changed fact](counterfactual-consumer.png)."
        ),
        "",
    ]
    return "\n".join(lines)


def build() -> None:
    if OUTPUT.exists():
        raise FileExistsError("evidence already exists; use --verify or a new version")
    sources = source_digests()
    tasks = []
    for seed in (*range(5000, 5024), *COUNTERFACTUAL_SEEDS):
        task = generate_memory_task(seed)
        row = validate_task_admission(task, backend_factory=MemoryBackend)
        row["canonical_task_digest"] = content_digest(task.canonical_dict())
        tasks.append(row)
        if len(tasks) % 12 == 0:
            print(f"admission completed: {len(tasks)}/72", flush=True)
    counterfactuals = []
    positions: Counter[int] = Counter()
    for seed in range(5000, 5024):
        base = generate_memory_task(seed)
        for index in (5, 7):
            stage = base.stages[index]
            positions.update(
                [
                    next(
                        i
                        for i, c in enumerate(stage.controls)
                        if c.control_id == stage.target_control_id
                    )
                ]
            )
        for task, source, consumer in zip(development_counterfactuals(seed), (0, 2), (5, 7)):
            source_difference = pixel_difference(
                frame_at(seed, source), frame_at(task.seed, source)
            )
            consumer_difference = pixel_difference(
                frame_at(seed, consumer), frame_at(task.seed, consumer)
            )
            if (
                source_difference["differing_pixel_count"] == 0
                or consumer_difference != {"differing_pixel_count": 0, "max_per_channel_delta": 0}
                or base.stages[consumer].target_control_id
                == task.stages[consumer].target_control_id
                or base.instruction != task.instruction
            ):
                raise ValueError("counterfactual admission failed")
            counterfactuals.append(
                {
                    "base_seed": seed,
                    "counterfactual_seed": task.seed,
                    "source_index": source,
                    "consumer_index": consumer,
                    "source_pixels": source_difference,
                    "consumer_pixels": consumer_difference,
                    "base_target": base.stages[consumer].target_control_id,
                    "changed_target": task.stages[consumer].target_control_id,
                    "identical_task_instruction": True,
                }
            )
    baselines = {
        rule: [baseline(seed, rule) for seed in range(5000, 5024)]
        for rule in ("position-0", "position-1", "position-2", "label-min", "label-max")
    }
    value = {
        "schema_version": "pixelgym-v5-d58-memory-admission-v1",
        "generator_version": MEMORY_GENERATOR_VERSION,
        "source_binding_digest": content_digest(sources),
        "tasks": tasks,
        "counterfactuals": counterfactuals,
        "baselines": baselines,
        "summary": {
            "admitted_task_count": len(tasks),
            "counterfactual_pair_count": len(counterfactuals),
            "memory_target_positions": {
                str(key): count for key, count in sorted(positions.items())
            },
            "baselines": {
                rule: {
                    "successes": sum(row["success"] for row in rows),
                    "first_attempt_correct": sum(
                        choice["first_attempt_correct"] for row in rows for choice in row["choices"]
                    ),
                }
                for rule, rows in baselines.items()
            },
        },
        "provider_calls_made": 0,
        "new_spend_usd": "0",
        "confirmatory_tasks_generated": 0,
    }
    value["evidence_digest"] = content_digest(value)
    snapshot = json.loads((OUTPUT.parent / "gemini-price-snapshot.json").read_text())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    plan = pilot_plan(ROOT, snapshot=snapshot, code_revision=revision)
    plan["source_binding_digest"] = content_digest(sources)
    plan["plan_digest"] = content_digest(
        {key: item for key, item in plan.items() if key != "plan_digest"}
    )
    if source_digests() != sources:
        raise RuntimeError("source changed during admission")
    OUTPUT.mkdir()
    for name, data in (
        ("admission.json", value),
        ("sources.json", sources),
        ("pilot-plan.json", plan),
    ):
        (OUTPUT / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    (OUTPUT / "report.md").write_text(render_report(value))
    for name, seed, stage_index in (
        ("base-source", 5000, 0),
        ("counterfactual-source", 5200, 0),
        ("base-consumer", 5000, 5),
        ("counterfactual-consumer", 5200, 5),
    ):
        Image.fromarray(frame_at(seed, stage_index)).save(OUTPUT / f"{name}.png")
    checksums = {
        path.name: "sha256:" + sha256_bytes(path.read_bytes()) for path in sorted(OUTPUT.iterdir())
    }
    (OUTPUT / "files.json").write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")
    print(json.dumps(value["summary"], sort_keys=True), flush=True)


def verify() -> None:
    files = json.loads((OUTPUT / "files.json").read_text())
    for name, digest in files.items():
        if "sha256:" + sha256_bytes((OUTPUT / name).read_bytes()) != digest:
            raise ValueError(f"artifact bytes changed: {name}")
    sources = json.loads((OUTPUT / "sources.json").read_text())
    if sources != source_digests():
        raise ValueError("source binding is stale")
    value = json.loads((OUTPUT / "admission.json").read_text())
    if render_report(value) != (OUTPUT / "report.md").read_text():
        raise ValueError("report does not match stored evidence")
    print(
        "verified artifact hashes, current source binding, and evidence-only report; provider calls: 0"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify() if args.verify else build()


if __name__ == "__main__":
    main()
