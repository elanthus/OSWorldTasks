"""No-cost scripted policies and mutation traces for v5 admission."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import Stage, StageKind, V5Task

Action = dict[str, int]


def click_action(x: int, y: int) -> Action:
    return {"action_type": int(ActionType.CLICK), "x": x, "y": y, "key": 0}


def key_action(key: str) -> Action:
    return {
        "action_type": int(ActionType.KEY),
        "x": 0,
        "y": 0,
        "key": KEY_ALLOWLIST.index(key),
    }


def noop_action() -> Action:
    return {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}


class Mutation(StrEnum):
    MEMORYLESS = "memoryless_deferred_fact"
    SKIPPED_REVISION = "skipped_revision"
    REPEATED_INVALID_REPAIR = "repeated_invalid_repair"
    PREMATURE_COMMIT = "premature_commit"
    STALE_TASK_SUBMISSION = "stale_task_submission"
    STEP_BUDGET_EXHAUSTION = "step_budget_exhaustion"


@dataclass(frozen=True)
class ScriptedTrace:
    name: str
    actions: tuple[Action, ...]
    expected_success: bool
    expected_route: str


def golden_actions(task: V5Task, backend: V5FakeBackend) -> tuple[Action, ...]:
    if backend.task.task_id != task.task_id:
        raise ValueError("backend must be reset to the requested task")
    actions: list[Action] = []
    # Simulate only to query state-dependent recovery controls.  The caller's
    # backend is not mutated; a private planner backend produces the trace.
    planner = V5FakeBackend()
    planner.reset(task.seed)
    for stage in task.stages:
        _append_golden_stage(planner, stage, actions)
    planner.close()
    return tuple(actions)


def mutation_trace(task: V5Task, mutation: Mutation) -> ScriptedTrace:
    if mutation is Mutation.STEP_BUDGET_EXHAUSTION or mutation is Mutation.STALE_TASK_SUBMISSION:
        actions = tuple(noop_action() for _ in range(task.max_episode_steps))
    else:
        planner = V5FakeBackend()
        planner.reset(task.seed)
        prefix: list[Action] = []
        target_index = {
            Mutation.MEMORYLESS: 5,
            Mutation.SKIPPED_REVISION: len(task.stages) - 2,
            Mutation.REPEATED_INVALID_REPAIR: next(
                (
                    index
                    for index, stage in enumerate(task.stages)
                    if stage.recovery_stage
                ),
                5,
            ),
            Mutation.PREMATURE_COMMIT: 3,
        }[mutation]
        for index, stage in enumerate(task.stages):
            if index == target_index and mutation is Mutation.REPEATED_INVALID_REPAIR:
                if stage.recovery_stage:
                    action = click_action(*planner.control_center(stage.target_control_id))
                    prefix.append(action)
                    planner.click(*planner.control_center(stage.target_control_id))
                prefix.append(click_action(5, 5))
                break
            if index == target_index:
                wrong = next(
                    control
                    for control in planner.visible_controls()
                    if control.control_id != stage.target_control_id
                )
                prefix.append(click_action(*wrong.center))
                break
            _append_golden_stage(planner, stage, prefix)
        actions = tuple(prefix) + tuple(
            noop_action() for _ in range(max(0, task.max_episode_steps - len(prefix)))
        )
        planner.close()
    return ScriptedTrace(mutation.value, actions, False, mutation.value)


def all_mutation_traces(task: V5Task) -> Iterable[ScriptedTrace]:
    for mutation in Mutation:
        yield mutation_trace(task, mutation)


def _append_golden_stage(
    planner: V5FakeBackend, stage: Stage, actions: list[Action]
) -> None:
    if stage.kind is StageKind.TEXT:
        center = planner.control_center("text_input")
        actions.append(click_action(*center))
        planner.click(*center)
        for character in stage.required_text:
            actions.append(key_action(character))
            planner.key(character)
        center = planner.control_center("continue")
        actions.append(click_action(*center))
        planner.click(*center)
        return
    center = planner.control_center(stage.target_control_id)
    actions.append(click_action(*center))
    planner.click(*center)
    if stage.recovery_stage:
        center = planner.control_center("repair_implicated")
        actions.append(click_action(*center))
        planner.click(*center)
