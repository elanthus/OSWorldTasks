"""Reward-timing trajectories required by the Day 2 validation plan."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form.ui import TEXT_WIDGETS, WidgetId

_KEY_INDEX = {value: index for index, value in enumerate(KEY_ALLOWLIST)}


def _action(action_type: ActionType, *, x: int = 0, y: int = 0, key: int = 0) -> dict:
    return {"action_type": int(action_type), "x": x, "y": y, "key": key}


def _click(point: tuple[int, int]) -> dict:
    return _action(ActionType.CLICK, x=point[0], y=point[1])


def _key(value: str) -> dict:
    return _action(ActionType.KEY, key=_KEY_INDEX[value])


def build_value_actions(
    backend: FakeBackend,
    values: Mapping[str, Any],
    *,
    include_submit: bool,
) -> list[dict[str, int]]:
    """Produce public CLICK/KEY actions for explicit form values."""

    actions: list[dict[str, int]] = []
    for widget in TEXT_WIDGETS:
        actions.append(_click(backend.layout.controls[widget].center))
        actions.extend(_key(character) for character in values[widget.value])

    actions.append(_click(backend.layout.controls[WidgetId.COUNTRY].center))
    country_index = backend.form.country_options.index(values["country"])
    actions.append(_click(backend.layout.country_options[country_index].center))
    payment_index = backend.form.payment_options.index(values["payment_terms"])
    actions.append(_click(backend.layout.payment_options[payment_index].center))
    if values["expedited_onboarding"]:
        actions.append(_click(backend.layout.controls[WidgetId.EXPEDITED_ONBOARDING].center))
    if include_submit:
        actions.append(_click(backend.layout.controls[WidgetId.SUBMIT].center))
    return actions


def _execute(
    name: str,
    env: PixelGuiEnv,
    actions: Sequence[Mapping[str, Any]],
    *,
    expected_outcome: str,
) -> dict[str, Any]:
    first_positive = None
    terminal_step = None
    truncation_step = None
    reward_count = 0
    for step, action in enumerate(actions, start=1):
        _obs, reward, terminated, truncated, _info = env.step(action)
        if reward > 0:
            reward_count += 1
            if first_positive is None:
                first_positive = step
        if terminated:
            terminal_step = step
        if truncated:
            truncation_step = step
        if terminated or truncated:
            break
    observed = (
        "terminal_reward"
        if reward_count == 1 and terminal_step is not None
        else "truncated_without_reward"
        if truncation_step is not None and reward_count == 0
        else "no_reward"
        if reward_count == 0
        else "invalid_reward_timeline"
    )
    return {
        "name": name,
        "action_count": len(actions),
        "first_positive_reward_step": first_positive,
        "terminal_step": terminal_step,
        "truncation_step": truncation_step,
        "positive_reward_count": reward_count,
        "expected_outcome": expected_outcome,
        "observed_outcome": observed,
        "passed": observed == expected_outcome,
    }


def _run_static(
    name: str,
    actions: Sequence[Mapping[str, Any]],
    *,
    expected_outcome: str,
    seed: int,
    max_episode_steps: int = 200,
) -> dict[str, Any]:
    env = PixelGuiEnv(FakeBackend(), max_episode_steps=max_episode_steps)
    try:
        env.reset(seed=seed)
        return _execute(name, env, actions, expected_outcome=expected_outcome)
    finally:
        env.close()


def _run_values(
    name: str,
    values: Mapping[str, Any],
    *,
    include_submit: bool,
    expected_outcome: str,
    seed: int,
) -> dict[str, Any]:
    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    try:
        env.reset(seed=seed)
        actions = build_value_actions(backend, values, include_submit=include_submit)
        return _execute(name, env, actions, expected_outcome=expected_outcome)
    finally:
        env.close()


def validate_reward_timing(golden_fixture: Path) -> dict[str, Any]:
    fixture = json.loads(golden_fixture.read_text(encoding="utf-8"))
    seed = fixture["seed"]
    golden = fixture["actions"]
    expected = FakeBackend().reset(seed)["fields"]
    records: list[dict[str, Any]] = []

    empty = FakeBackend()
    empty.reset(seed)
    empty_submit = [_click(empty.layout.controls[WidgetId.SUBMIT].center)]
    records.append(
        _run_static("empty-submit", empty_submit, expected_outcome="no_reward", seed=seed)
    )
    records.append(
        _run_values(
            "correct-fields-without-submit",
            expected,
            include_submit=False,
            expected_outcome="no_reward",
            seed=seed,
        )
    )

    for field, original in expected.items():
        near_miss = dict(expected)
        if field in {widget.value for widget in TEXT_WIDGETS}:
            near_miss[field] = f"{original}X"
        elif field == "country":
            options = list(empty.form.country_options)
            near_miss[field] = options[(options.index(original) + 1) % len(options)]
        elif field == "payment_terms":
            options = list(empty.form.payment_options)
            near_miss[field] = options[(options.index(original) + 1) % len(options)]
        else:
            near_miss[field] = not original
        records.append(
            _run_values(
                f"near-miss-{field}",
                near_miss,
                include_submit=True,
                expected_outcome="no_reward",
                seed=seed,
            )
        )

    stale_backend = FakeBackend()
    stale_env = PixelGuiEnv(stale_backend)
    try:
        stale_env.reset(seed=seed)
        stale_backend.install_submission(expected, task_id="vf-stale-task")
        records.append(
            _execute(
                "wrong-or-stale-task-id-fixture",
                stale_env,
                [_action(ActionType.NOOP)],
                expected_outcome="no_reward",
            )
        )
    finally:
        stale_env.close()

    for prefix_length in range(len(golden)):
        records.append(
            _run_static(
                f"golden-prefix-{prefix_length:03d}",
                golden[:prefix_length],
                expected_outcome="no_reward",
                seed=seed,
            )
        )
    records.append(
        _run_static(
            "complete-golden-trajectory",
            golden,
            expected_outcome="terminal_reward",
            seed=seed,
        )
    )

    duplicate_backend = FakeBackend()
    duplicate_env = PixelGuiEnv(duplicate_backend)
    try:
        duplicate_env.reset(seed=seed)
        duplicate = _execute(
            "duplicate-submit-after-success",
            duplicate_env,
            golden,
            expected_outcome="terminal_reward",
        )
        try:
            duplicate_env.step(golden[-1])
        except RuntimeError:
            duplicate["post_success_step_rejected"] = True
        else:
            duplicate["post_success_step_rejected"] = False
        duplicate["passed"] = duplicate["passed"] and duplicate["post_success_step_rejected"]
        records.append(duplicate)
    finally:
        duplicate_env.close()

    records.append(
        _run_static(
            "timeout-one-action-before-completion",
            golden[:-1],
            expected_outcome="truncated_without_reward",
            seed=seed,
            max_episode_steps=len(golden) - 1,
        )
    )

    return {
        "schema_version": 1,
        "validator": "reward-timing",
        "backend": "fake",
        "seed": seed,
        "records": records,
        "summary": {
            "trajectory_count": len(records),
            "passed_count": sum(record["passed"] for record in records),
            "failed_count": sum(not record["passed"] for record in records),
            "golden_prefix_count": len(golden),
            "near_miss_count": len(expected),
            "passed": all(record["passed"] for record in records),
        },
    }
