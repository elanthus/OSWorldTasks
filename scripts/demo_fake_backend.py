#!/usr/bin/env python3
"""Run PixelGuiEnv against the fake backend and print the reward trace (D1.6).

The scripted interaction fills part of the vendor form and then presses Submit
with the form still incomplete. Every reward stays `0.0` and the episode does
not terminate -- which is the point worth demonstrating: pressing Submit is not
what pays out. Reward comes only from the privileged host-side evaluator
agreeing that the submitted values exactly match the task.

The successful, reward-`1.0` counterpart is the golden trajectory (D1.7).

Usage:
    python scripts/demo_fake_backend.py [--seed 7] [--screenshot PATH]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form.ui import WidgetId

_KEY_INDEX = {key: index for index, key in enumerate(KEY_ALLOWLIST)}


def _click(x: int, y: int) -> dict[str, Any]:
    return {"action_type": ActionType.CLICK, "x": x, "y": y, "key": 0}


def _key(key: str) -> dict[str, Any]:
    return {"action_type": ActionType.KEY, "x": 0, "y": 0, "key": _KEY_INDEX[key]}


def _scripted_actions(backend: FakeBackend) -> list[dict[str, Any]]:
    layout = backend.layout
    actions = [_click(*layout.controls[WidgetId.COMPANY_NAME].center)]
    actions += [_key(character) for character in "Blue Harbor Supply Co."]
    actions.append(_key("Tab"))  # move on to Contact email
    actions += [_key(character) for character in "onboarding@example"]
    actions.append(_click(*layout.controls[WidgetId.EXPEDITED_ONBOARDING].center))
    actions.append(_click(*layout.payment_options[0].center))
    actions.append(_click(*layout.controls[WidgetId.SUBMIT].center))  # incomplete
    return actions


def _describe(action: dict[str, Any]) -> str:
    action_type = ActionType(int(action["action_type"]))
    if action_type is ActionType.CLICK:
        return f"CLICK({action['x']},{action['y']})"
    if action_type is ActionType.KEY:
        return f"KEY({KEY_ALLOWLIST[action['key']]!r})"
    return "NOOP"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--screenshot", help="write the final frame to this PNG path")
    args = parser.parse_args()

    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    observation, info = env.reset(seed=args.seed)
    print(f"task_id={info['task_id']}  observation={observation.shape} {observation.dtype}")
    print(f"{'step':>4}  {'action':<28} {'reward':>6}  terminated  truncated")

    total = 0.0
    for step, action in enumerate(_scripted_actions(backend), start=1):
        observation, reward, terminated, truncated, _info = env.step(action)
        total += reward
        print(f"{step:>4}  {_describe(action):<28} {reward:>6.1f}  {terminated!s:<10}  {truncated}")
        if terminated or truncated:
            break

    print(f"\ntotal reward: {total}")
    print("submissions recorded:", len(backend.read_submissions()))
    print(
        "Submit was pressed with an incomplete form, so the evaluator refused it "
        "and the episode continues."
    )

    if args.screenshot:
        from PIL import Image

        Image.fromarray(observation).save(args.screenshot)
        print(f"final frame written to {args.screenshot}")

    env.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
