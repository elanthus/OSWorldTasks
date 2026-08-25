"""Deterministic six-family task generator for ``pixelgym-agent-v5``."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any

from pixelgym.grounding.v5.contracts import (
    GENERATOR_VERSION,
    PROTOCOL_VERSION,
    Control,
    DifficultyBand,
    Partition,
    SeedRecord,
    Stage,
    StageKind,
    V5Task,
    WorkflowFamily,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.seeds import SEED_RECORD_BY_SEED, SEED_RECORDS, validate_seed_contract
from pixelgym.serialization import canonical_json_bytes

_TOKENS = ("A17", "B24", "C31", "D48", "E52", "F69", "G73", "H86")
_CODES = ("K2M4", "P7R1", "T3V8", "W6X2", "Y4Z9", "B5D7")
_VENDORS = ("Aster", "Birch", "Cedar", "Dover", "Elm", "Flint", "Grove", "Harbor")


def _semantic_index(record: SeedRecord) -> int:
    return int(hashlib.sha256(record.logical_id.encode()).hexdigest()[:8], 16)


def _controls(target: str, labels: tuple[str, str, str], *, twin: bool) -> tuple[Control, ...]:
    ids = (target, f"{target}_near", f"{target}_wrong")
    items = tuple(Control(control_id, label) for control_id, label in zip(ids, labels))
    return (items[2], items[0], items[1]) if twin else items


def _click_stage(
    index: int,
    *,
    heading: str,
    instruction: str,
    facts: tuple[str, ...],
    target: str,
    labels: tuple[str, str, str],
    twin: bool,
    critical: bool = False,
    dependency_id: str | None = None,
    recovery_stage: bool = False,
    kind: StageKind = StageKind.CLICK,
) -> Stage:
    return Stage(
        stage_id=f"stage-{index:02d}",
        kind=kind,
        heading=heading,
        instruction=instruction,
        facts=facts,
        controls=_controls(target, labels, twin=twin),
        target_control_id=target,
        critical=critical,
        dependency_id=dependency_id,
        recovery_stage=recovery_stage,
    )


def _text_stage(
    index: int, *, heading: str, instruction: str, fact: str, value: str, twin: bool
) -> Stage:
    controls: tuple[Control, ...] = (
        Control("text_input", "Short code", "input"),
        Control("continue", "Continue", "continue"),
    )
    if twin:
        controls = tuple(reversed(controls))
    return Stage(
        stage_id=f"stage-{index:02d}",
        kind=StageKind.TEXT,
        heading=heading,
        instruction=instruction,
        facts=(fact,),
        controls=controls,
        target_control_id="continue",
        required_text=value,
    )


def _family_language(family: WorkflowFamily) -> tuple[str, str]:
    return {
        WorkflowFamily.EVIDENCE_AGGREGATION: (
            "Aggregate the request evidence",
            "Combine the visible request and verification facts before committing.",
        ),
        WorkflowFamily.DEFERRED_JOIN: (
            "Join deferred vendor references",
            "Retain both references and apply their conjunction at the later checks.",
        ),
        WorkflowFamily.CONDITIONAL_PRECEDENCE: (
            "Apply onboarding precedence",
            "Apply the visible base rule, exception, and precedence rule to this case.",
        ),
        WorkflowFamily.REVISION_AFTER_REVEAL: (
            "Revise the provisional route",
            "Make a reversible choice, inspect the reveal, and revise if required.",
        ),
        WorkflowFamily.VISIBLE_ERROR_RECOVERY: (
            "Recover the rejected onboarding",
            "Use the visible error to repair only the implicated entry, then continue.",
        ),
        WorkflowFamily.REVIEW_AND_COMMIT: (
            "Review and safely commit",
            "Verify the summary, correct the plausible mismatch, and commit once.",
        ),
    }[family]


def _build_stages(record: SeedRecord) -> tuple[Stage, ...]:
    logical = _semantic_index(record)
    twin = record.variant == "twin_b"
    first_ref = _TOKENS[logical % len(_TOKENS)]
    second_ref = _TOKENS[(logical // 7 + 3) % len(_TOKENS)]
    code_a = _CODES[(logical // 11) % len(_CODES)]
    code_b = _CODES[(logical // 17 + 2) % len(_CODES)]
    vendor = _VENDORS[(logical // 19) % len(_VENDORS)]
    count = {
        DifficultyBand.REGRESSION: 10,
        DifficultyBand.FRONTIER: 12,
        DifficultyBand.CEILING: 14,
    }[record.difficulty_band]
    family = record.family
    stages: list[Stage] = [
        _click_stage(
            0,
            heading="Inspect the incoming request",
            instruction="Open the request matching the active vendor.",
            facts=(f"Active vendor: {vendor}.", f"Request reference: {first_ref}."),
            target="open_request",
            labels=(f"Open {vendor} request", "Open archived request", "Open template"),
            twin=twin,
            dependency_id="request_ref",
        ),
        _text_stage(
            1,
            heading="Record the request code",
            instruction="Enter the short request code exactly as shown.",
            fact=f"Request code: {code_a}.",
            value=code_a,
            twin=twin,
        ),
        _click_stage(
            2,
            heading="Inspect verification evidence",
            instruction="Open the current verification record.",
            facts=(f"Verification reference: {second_ref}.", "Older records are distractors."),
            target="open_verification",
            labels=("Open current verification", "Open expired verification", "Skip verification"),
            twin=twin,
            dependency_id="verification_ref",
        ),
        _click_stage(
            3,
            heading="Choose a provisional route",
            instruction="Select the reversible route indicated by the visible base rule.",
            facts=("Base rule: verified vendors start in standard review.",),
            target="provisional_standard",
            labels=("Provisional standard review", "Immediate approval", "Reject request"),
            twin=twin,
        ),
        _text_stage(
            4,
            heading="Record the verification code",
            instruction="Enter the short verification code exactly as shown.",
            fact=f"Verification code: {code_b}.",
            value=code_b,
            twin=twin,
        ),
        _click_stage(
            5,
            heading="Apply the retained request reference",
            instruction="Choose the row matching the earlier request reference.",
            facts=("The source request is no longer visible.",),
            target=f"match_{first_ref.lower()}",
            labels=(f"Match row {first_ref}", f"Match row {second_ref}", "Match newest row"),
            twin=twin,
            critical=True,
            dependency_id="request_ref",
        ),
        _click_stage(
            6,
            heading="Reveal the exception evidence",
            instruction="Open the exception notice before finalizing the provisional route.",
            facts=("A later notice may override the base rule.",),
            target="reveal_exception",
            labels=("Reveal exception notice", "Keep provisional route", "Dismiss notice"),
            twin=twin,
        ),
        _click_stage(
            7,
            heading="Join the retained verification reference",
            instruction="Resolve the consumer using the earlier verification reference.",
            facts=("Use the reference retained before the intervening decisions.",),
            target=f"join_{second_ref.lower()}",
            labels=(f"Use verification {second_ref}", f"Use request {first_ref}", "Use latest record"),
            twin=twin,
            critical=True,
            dependency_id="verification_ref",
            recovery_stage=family is WorkflowFamily.VISIBLE_ERROR_RECOVERY,
        ),
    ]

    middle_count = count - 10
    for offset in range(middle_count):
        index = 8 + offset
        stages.append(
            _click_stage(
                index,
                heading=f"Resolve precedence check {offset + 1}",
                instruction="Apply the visible exception before the base rule.",
                facts=(
                    "Exception: matched verification routes to enhanced review.",
                    "Precedence: the exception overrides the base rule.",
                ),
                target=f"apply_exception_{offset}",
                labels=("Apply enhanced review", "Keep standard review", "Reset all fields"),
                twin=twin,
                critical=family is WorkflowFamily.CONDITIONAL_PRECEDENCE,
            )
        )

    review_index = count - 2
    stages.extend(
        (
            _click_stage(
                review_index,
                heading="Review the completed summary",
                instruction="Correct the summary route using the revealed exception.",
                facts=(
                    f"Request {first_ref} is joined with verification {second_ref}.",
                    "The summary currently shows the plausible base-rule route.",
                ),
                target="correct_summary",
                labels=("Correct to enhanced review", "Accept standard review", "Clear summary"),
                twin=twin,
                critical=True,
                kind=StageKind.REVIEW,
            ),
            _click_stage(
                count - 1,
                heading="Final irreversible commit",
                instruction="Commit only the verified, corrected onboarding summary.",
                facts=("This commit is final and cannot be repaired after submission.",),
                target="commit_verified",
                labels=("Commit verified onboarding", "Commit stale draft", "Discard evidence"),
                twin=twin,
                critical=True,
                kind=StageKind.COMMIT,
            ),
        )
    )
    if family is WorkflowFamily.REVISION_AFTER_REVEAL:
        # The review stage is the explicit revision consumer for this family.
        stages[review_index] = Stage(
            **{
                **stages[review_index].__dict__,
                "heading": "Revise after the exception reveal",
                "instruction": "Replace the provisional base-rule route with the revealed exception.",
            }
        )
    return tuple(stages)


def generate_task(seed: int) -> V5Task:
    if type(seed) is not int:
        raise TypeError("v5 seed must be a plain int")
    try:
        record = SEED_RECORD_BY_SEED[seed]
    except KeyError as exc:
        raise ValueError(f"seed {seed} is outside the frozen v5 partitions") from exc
    title, instruction = _family_language(record.family)
    stages = _build_stages(record)
    recovery_actions = sum(stage.recovery_stage for stage in stages)
    optimal = len(stages) + sum(len(stage.required_text) + 1 for stage in stages if stage.required_text)
    optimal += recovery_actions
    slack = max(6, math.ceil(0.25 * optimal))

    # Twins share this identity because variant-only wording, order, and layout
    # are deliberately excluded.  Targets and underlying values remain bound.
    semantic_record = {
        "logical_id": record.logical_id,
        "family": record.family.value,
        "targets": [stage.target_control_id for stage in stages],
        "required_text": [stage.required_text for stage in stages],
    }
    semantic_digest = content_digest(semantic_record)
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed_record": record.to_dict(),
        "instruction": instruction,
        "title": title,
        "stages": [stage.privileged_dict() for stage in stages],
        "optimal_low_level_actions": optimal,
        "correction_slack": slack,
        "max_episode_steps": optimal + slack,
        "expected_result": f"completed-{seed}",
        "semantic_digest": semantic_digest,
    }
    task_id = "v5-" + sha256_bytes(canonical_json_bytes(identity))[:24]
    return V5Task(
        seed_record=record,
        instruction=instruction,
        title=title,
        stages=stages,
        optimal_low_level_actions=optimal,
        correction_slack=slack,
        max_episode_steps=optimal + slack,
        expected_result=f"completed-{seed}",
        semantic_digest=semantic_digest,
        task_id=task_id,
    )


def tasks_for_partition(partition: Partition) -> tuple[V5Task, ...]:
    return tuple(generate_task(record.seed) for record in SEED_RECORDS if record.partition is partition)


def validate_generator() -> dict[str, Any]:
    seed_summary = validate_seed_contract()
    tasks = tuple(generate_task(record.seed) for record in SEED_RECORDS)
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("v5 task IDs are not unique")
    pair_groups: dict[str, list[V5Task]] = {}
    for task in tasks:
        if task.seed_record.robustness_pair:
            pair_groups.setdefault(task.seed_record.logical_id, []).append(task)
    if any(len(group) != 2 for group in pair_groups.values()):
        raise ValueError("every robustness logical ID must have exactly two variants")
    if any(len({task.semantic_digest for task in group}) != 1 for group in pair_groups.values()):
        raise ValueError("robustness twins do not preserve semantic identity")
    return {
        **seed_summary,
        "task_count": len(tasks),
        "family_counts": dict(Counter(task.family.value for task in tasks)),
        "generator_manifest_digest": content_digest(
            [task.canonical_dict() for task in tasks]
        ),
    }
