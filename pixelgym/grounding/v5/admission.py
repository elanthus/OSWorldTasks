"""No-cost determinism, mutation, replay, and admission evidence for v5."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST
from pixelgym.env import PixelGuiEnv
from pixelgym.evaluator import evaluate
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    Partition,
    StageKind,
    V5Task,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.generator import tasks_for_partition
from pixelgym.grounding.v5.policies import (
    Action,
    Mutation,
    click_action,
    golden_actions,
    key_action,
    mutation_trace,
)
from pixelgym.task_spec import TaskSpec


@dataclass(frozen=True)
class ReplayOutcome:
    rewards: tuple[float, ...]
    terminated: bool
    truncated: bool
    screenshot_digests: tuple[str, ...]
    semantic_state_digests: tuple[str, ...]
    diagnostic_events: tuple[str, ...]
    stale_submission_validation: tuple[bool, bool, bool] | None

    @property
    def success(self) -> bool:
        return sum(self.rewards) == 1.0 and self.terminated and not self.truncated


def replay_actions(
    task: V5Task,
    actions: tuple[Action, ...],
    *,
    stale_submission: bool = False,
    backend_factory: Callable[[], V5FakeBackend] = V5FakeBackend,
) -> ReplayOutcome:
    backend = backend_factory()
    env = PixelGuiEnv(
        backend,
        instruction=task.instruction,
        max_episode_steps=task.max_episode_steps,
    )
    rewards: list[float] = []
    screenshots: list[str] = []
    states: list[str] = []
    diagnostics: list[str] = []
    terminated = False
    truncated = False
    stale_validation: tuple[bool, bool, bool] | None = None
    try:
        observation, _info = env.reset(seed=task.seed)
        if stale_submission:
            backend.install_submission(
                {"workflow_result": task.expected_result},
                task_id="v5-stale-task",
                seed=task.seed - 1,
            )
            task_spec = TaskSpec.from_generated(
                task.generated_record(),
                instruction=task.instruction,
                app_url=backend.app_url,
                max_episode_steps=task.max_episode_steps,
            )
            validation = evaluate(task_spec, backend.read_submissions())
            stale_validation = (
                validation.submitted,
                validation.task_id_matches,
                validation.success,
            )
        screenshots.append("sha256:" + sha256_bytes(observation.tobytes()))
        states.append("sha256:" + sha256_bytes(backend.checkpoint()))
        diagnostics.append(backend.read_privileged_diagnostic()["event"])
        for action in actions:
            observation, reward, terminated, truncated, _info = env.step(action)
            rewards.append(reward)
            screenshots.append("sha256:" + sha256_bytes(observation.tobytes()))
            states.append("sha256:" + sha256_bytes(backend.checkpoint()))
            diagnostics.append(backend.read_privileged_diagnostic()["event"])
            if terminated or truncated:
                break
    finally:
        env.close()
    return ReplayOutcome(
        rewards=tuple(rewards),
        terminated=terminated,
        truncated=truncated,
        screenshot_digests=tuple(screenshots),
        semantic_state_digests=tuple(states),
        diagnostic_events=tuple(diagnostics),
        stale_submission_validation=stale_validation,
    )


def recovery_actions(
    task: V5Task, *, backend_factory: Callable[[], V5FakeBackend] = V5FakeBackend,
) -> tuple[Action, ...]:
    planner = backend_factory()
    planner.reset(task.seed)
    actions: list[Action] = []
    first = task.stages[0]
    wrong = next(
        control
        for control in planner.visible_controls()
        if control.control_id != first.target_control_id
    )
    actions.append(click_action(*wrong.center))
    planner.click(*wrong.center)
    correct_center = planner.control_center(first.target_control_id)
    actions.append(click_action(*correct_center))
    planner.click(*correct_center)
    for stage in task.stages[1:]:
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
        else:
            center = planner.control_center(stage.target_control_id)
            actions.append(click_action(*center))
            planner.click(*center)
            if stage.recovery_stage:
                center = planner.control_center("repair_implicated")
                actions.append(click_action(*center))
                planner.click(*center)
    planner.close()
    return tuple(actions)


def random_floor_actions(task: V5Task, *, seed: int) -> tuple[Action, ...]:
    rng = random.Random(seed)
    actions: list[Action] = []
    for _ in range(task.max_episode_steps):
        action_type = rng.randrange(3)
        actions.append(
            {
                "action_type": action_type,
                "x": rng.randrange(1024),
                "y": rng.randrange(768),
                "key": rng.randrange(len(KEY_ALLOWLIST)),
            }
        )
    return tuple(actions)


def validate_task_admission(
    task: V5Task, *, backend_factory: Callable[[], V5FakeBackend] = V5FakeBackend,
) -> dict[str, Any]:
    backend = backend_factory()
    first_record = backend.reset(task.seed)
    first_frame = backend.screenshot()
    backend.install_submission({"workflow_result": task.expected_result})
    second_record = backend.reset(task.seed)
    second_frame = backend.screenshot()
    if first_record != second_record or not (first_frame == second_frame).all():
        raise ValueError("same-seed reset is not deterministic")
    if backend.read_submissions():
        raise ValueError("same-seed reset did not clear submissions")
    golden = golden_actions(task, backend)
    backend.close()
    if len(golden) != task.optimal_low_level_actions:
        raise ValueError("golden trace does not match the frozen optimal horizon")
    golden_first = replay_actions(task, golden, backend_factory=backend_factory)
    golden_second = replay_actions(task, golden, backend_factory=backend_factory)
    if not golden_first.success or golden_first != golden_second:
        raise ValueError("golden replay is unsuccessful or nondeterministic")
    recovery = recovery_actions(task, backend_factory=backend_factory)
    recovery_first = replay_actions(task, recovery, backend_factory=backend_factory)
    recovery_second = replay_actions(task, recovery, backend_factory=backend_factory)
    if not recovery_first.success or recovery_first != recovery_second:
        raise ValueError("declared recovery replay is unsuccessful or nondeterministic")
    mutations: dict[str, Any] = {}
    for mutation in Mutation:
        trace = mutation_trace(task, mutation, backend_factory=backend_factory)
        stale_submission = mutation is Mutation.STALE_TASK_SUBMISSION
        first = replay_actions(
            task,
            trace.actions,
            stale_submission=stale_submission,
            backend_factory=backend_factory,
        )
        second = replay_actions(
            task,
            trace.actions,
            stale_submission=stale_submission,
            backend_factory=backend_factory,
        )
        if first != second:
            raise ValueError(f"mutation {mutation.value} is nondeterministic")
        if any(first.rewards) or first.terminated or not first.truncated:
            raise ValueError(f"mutation {mutation.value} did not fail through truncation")
        if stale_submission and first.stale_submission_validation != (True, False, False):
            raise ValueError("stale-task submission was not explicitly rejected by evaluator")
        validation = first.stale_submission_validation
        mutations[mutation.value] = {
            "action_count": len(first.rewards),
            "expected_route": trace.expected_route,
            "terminal_classification": "step_limit_truncation",
            "last_diagnostic_event": first.diagnostic_events[-1],
            "stale_submission_validation": (
                None
                if validation is None
                else {
                    "submitted": validation[0],
                    "task_id_matches": validation[1],
                    "success": validation[2],
                    "rejected": validation == (True, False, False),
                }
            ),
            "trace_digest": content_digest(
                {
                    "schema_version": "pixelgym-agent-v5-mutation-trace-binding-v1",
                    "task_id": task.task_id,
                    "mutation": mutation.value,
                    "expected_route": trace.expected_route,
                    "replay_parameters": {
                        "seed": task.seed,
                        "max_episode_steps": task.max_episode_steps,
                        "stale_submission": stale_submission,
                    },
                    "actions": trace.actions,
                }
            ),
        }
    floor = replay_actions(task, random_floor_actions(task, seed=task.seed ^ 0x5A5A),
                           backend_factory=backend_factory)
    if any(floor.rewards):
        raise ValueError("frozen random-action floor unexpectedly succeeded")
    return {
        "task_id": task.task_id,
        "seed": task.seed,
        "family": task.family.value,
        "golden": {
            "action_count": len(golden_first.rewards),
            "reward_sum": sum(golden_first.rewards),
            "trace_digest": content_digest(golden),
            "screenshot_chain_digest": content_digest(golden_first.screenshot_digests),
        },
        "recovery": {
            "action_count": len(recovery_first.rewards),
            "reward_sum": sum(recovery_first.rewards),
            "trace_digest": content_digest(recovery),
        },
        "mutations": mutations,
        "random_floor_reward_sum": sum(floor.rewards),
    }


def build_admission_evidence(
    partition: Partition = Partition.DEVELOPMENT,
) -> dict[str, Any]:
    task_records = [validate_task_admission(task) for task in tasks_for_partition(partition)]
    return {
        "schema_version": "pixelgym-agent-v5-admission-v1",
        "partition": partition.value,
        "task_count": len(task_records),
        "tasks": task_records,
        "evidence_digest": content_digest(task_records),
        "provider_calls_made": 0,
    }
