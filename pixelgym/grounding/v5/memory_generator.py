"""Opt-in D5.8 successor with two genuinely deferred, visually ambiguous choices.

The historical generator and its seed records remain unchanged. Counterfactual
development seeds change a source fact while preserving the consumer's pixels.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from pixelgym.grounding.v5.contracts import (
    Control,
    DifficultyBand,
    Partition,
    SeedRecord,
    V5Task,
    WorkflowFamily,
    content_digest,
)
from pixelgym.grounding.v5.generator import _build_stages, _family_language
from pixelgym.grounding.v5.seeds import SEED_RECORD_BY_SEED

MEMORY_GENERATOR_VERSION = "pixelgym-agent-v5-generator-memory-v2"
MEMORY_TASK_SCHEMA_VERSION = "pixelgym-agent-v5-task-memory-v2"
COUNTERFACTUAL_SEEDS = tuple(range(5200, 5248))
ADDITIONAL_CONFIRMATORY_SEEDS = tuple(range(6096, 6144))
CONSUMERS = ((0, 5, "request"), (2, 7, "verification"))
TOKENS = ("A17", "B24", "C31", "D48", "E52", "F69", "G73", "H86")


def _number(*parts: object) -> int:
    return int(hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest(), 16)


def seed_record(seed: int) -> SeedRecord:
    if type(seed) is not int:
        raise TypeError("memory seed must be a plain int")
    if seed in COUNTERFACTUAL_SEEDS:
        original = SEED_RECORD_BY_SEED[5000 + (seed - 5200) // 2]
        source = "request" if seed % 2 == 0 else "verification"
        return replace(original, seed=seed, logical_id=f"{original.logical_id}-cf-{source}")
    if seed in ADDITIONAL_CONFIRMATORY_SEEDS:
        family_index, offset = divmod(seed - 6096, 8)
        band = (
            DifficultyBand.REGRESSION
            if offset < 2
            else DifficultyBand.FRONTIER
            if offset < 6
            else DifficultyBand.CEILING
        )
        family = tuple(WorkflowFamily)[family_index]
        return SeedRecord(
            seed,
            Partition.CONFIRMATORY,
            family,
            16 + offset,
            f"confirmatory-{family.value}-logical-{12 + offset:02d}",
            "base",
            band,
        )
    try:
        return SEED_RECORD_BY_SEED[seed]
    except KeyError as exc:
        raise ValueError("seed is outside the frozen memory partitions") from exc


@dataclass(frozen=True)
class MemoryTask(V5Task):
    def canonical_dict(self) -> dict[str, Any]:
        return {
            **super().canonical_dict(),
            "schema_version": MEMORY_TASK_SCHEMA_VERSION,
            "generator_version": MEMORY_GENERATOR_VERSION,
        }

    def generated_record(self) -> dict[str, Any]:
        return {**super().generated_record(), "generator_version": MEMORY_GENERATOR_VERSION}


def permute_controls(
    controls: tuple[Control, ...], *, layout_key: str, stage_id: str, variant: str
) -> tuple[Control, ...]:
    """Permutation uses positions and a separate layout stream, never targets/labels."""
    order = sorted(
        range(len(controls)), key=lambda i: _number("layout-v2", layout_key, stage_id, i)
    )
    if variant == "twin_b":
        order = order[1:] + order[:1]
    return tuple(controls[index] for index in order)


def generate_memory_task(seed: int) -> MemoryTask:
    record = seed_record(seed)
    base_record = (
        SEED_RECORD_BY_SEED[5000 + (seed - 5200) // 2] if seed in COUNTERFACTUAL_SEEDS else record
    )
    # Start from the historical stage structure, with a canonical order. The
    # independent layout stream supplies every successor ordering, including twins.
    stages = list(_build_stages(replace(base_record, variant="base")))
    selected_tokens = sorted(
        TOKENS, key=lambda token: _number("candidates-v2", base_record.logical_id, token)
    )[:6]
    references = []
    for slot, (source, consumer, name) in enumerate(CONSUMERS):
        choices = selected_tokens[3 * slot : 3 * slot + 3]
        selected = _number("source-v2", base_record.logical_id, name) % 3
        if seed in COUNTERFACTUAL_SEEDS and (seed - 5200) % 2 == slot:
            selected = (selected + 1) % 3
        reference = choices[selected]
        references.append(reference)
        source_facts = stages[source].facts
        facts = (
            (source_facts[0], f"Request reference: {reference}.")
            if source == 0
            else (f"Verification reference: {reference}.", source_facts[1])
        )
        stages[source] = replace(stages[source], facts=facts)
        controls = tuple(
            Control(f"{name}-choice-{i}", f"Match row {value}") for i, value in enumerate(choices)
        )
        stages[consumer] = replace(
            stages[consumer],
            instruction=f"Choose the row matching the earlier {name} reference.",
            facts=(f"The source {name} reference is no longer visible.",),
            controls=controls,
            target_control_id=f"{name}-choice-{selected}",
        )
    review_index = len(stages) - 2
    stages[review_index] = replace(
        stages[review_index],
        facts=(
            f"Request {references[0]} is joined with verification {references[1]}.",
            stages[review_index].facts[1],
        ),
    )
    stages = [
        replace(
            stage,
            controls=permute_controls(
                stage.controls,
                layout_key=base_record.logical_id,
                stage_id=stage.stage_id,
                variant=record.variant,
            ),
        )
        for stage in stages
    ]
    title, instruction = _family_language(record.family)
    instruction += (
        " Remember the request and verification references. Each recorded reference choice is final;"
        " its correctness is checked only when the onboarding is submitted."
    )
    optimal = len(stages) + sum(
        len(stage.required_text) + 1 for stage in stages if stage.required_text
    )
    optimal += sum(stage.recovery_stage for stage in stages)
    slack = max(6, (optimal + 3) // 4)
    semantic_stages = []
    for stage in stages:
        value = stage.privileged_dict()
        value["controls"] = sorted(value["controls"], key=lambda c: c["control_id"])
        semantic_stages.append(value)
    task = MemoryTask(
        record,
        instruction,
        title,
        tuple(stages),
        optimal,
        slack,
        optimal + slack,
        f"completed-memory-v2-{seed}",
        content_digest({"logical_id": record.logical_id, "stages": semantic_stages}),
        "",
    )
    return replace(task, task_id="v5m-" + content_digest(task.canonical_dict())[7:31])


def development_counterfactuals(seed: int) -> tuple[MemoryTask, MemoryTask]:
    if type(seed) is not int or not 5000 <= seed <= 5023:
        raise ValueError("counterfactuals are restricted to development seeds")
    first = 5200 + 2 * (seed - 5000)
    return generate_memory_task(first), generate_memory_task(first + 1)
