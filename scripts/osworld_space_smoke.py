"""Run a bounded action/observation and empty-submit smoke on real OSWorld.

The valid sequence uses only NOOP and allowlisted keys.  It submits the
untouched form to cover the affordable real-backend reward-timing subset,
then proves malformed actions are rejected before the adapter forwards a
structured action to OSWorld.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST, ActionType, InvalidActionError
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv

DEFAULT_OUTPUT = Path("artifacts/day-2/raw/real-space-smoke.json")
_KEY_INDEX = {value: index for index, value in enumerate(KEY_ALLOWLIST)}


def _action(action_type: int, *, x: int = 0, y: int = 0, key: int = 0) -> dict[str, Any]:
    return {"action_type": action_type, "x": x, "y": y, "key": key}


def _key(value: str) -> dict[str, Any]:
    return _action(ActionType.KEY, key=_KEY_INDEX[value])


def run(guest_image: Path, output: Path) -> dict[str, Any]:
    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=guest_image))
    env = PixelGuiEnv(backend)
    observation_records: list[dict[str, Any]] = []
    provider_closed = False
    try:
        observation, info = env.reset(seed=7)
        observation_records.append(
            {
                "name": "reset",
                "shape": list(observation.shape),
                "dtype": str(observation.dtype),
                "contained": env.observation_space.contains(observation),
            }
        )

        # From the page-focused initial state, eight Tabs reach Submit.
        # Enter creates an empty privileged submission, which must not reward.
        valid_actions = [
            _action(ActionType.NOOP),
            *[_key("Tab") for _ in range(8)],
            _key("Enter"),
        ]
        reward_records = []
        for index, action in enumerate(valid_actions, start=1):
            observation, reward, terminated, truncated, _step_info = env.step(action)
            observation_records.append(
                {
                    "name": f"valid-action-{index}",
                    "shape": list(observation.shape),
                    "dtype": str(observation.dtype),
                    "contained": env.observation_space.contains(observation),
                }
            )
            reward_records.append(
                {
                    "step": index,
                    "reward": reward,
                    "terminated": terminated,
                    "truncated": truncated,
                }
            )

        submissions = backend.last_submissions
        blank_values = {
            "company_name": "",
            "contact_email": "",
            "contact_phone": "",
            "tax_id": "",
            "country": "",
            "payment_terms": "",
            "expedited_onboarding": False,
        }
        empty_submit_passed = (
            len(submissions) == 1
            and dict(submissions[0].values) == blank_values
            and all(item["reward"] == 0.0 for item in reward_records)
            and not any(item["terminated"] or item["truncated"] for item in reward_records)
        )

        invalid_actions = [
            ("negative-x", _action(ActionType.CLICK, x=-1)),
            ("x-equals-width", _action(ActionType.CLICK, x=env.observation_space.shape[1])),
            ("negative-y", _action(ActionType.CLICK, y=-1)),
            ("y-equals-height", _action(ActionType.CLICK, y=env.observation_space.shape[0])),
            ("negative-key", _action(ActionType.KEY, key=-1)),
            ("key-equals-count", _action(ActionType.KEY, key=len(KEY_ALLOWLIST))),
            ("unknown-action-type", _action(len(ActionType))),
            ("float-x", _action(ActionType.CLICK, x=1.0)),
            ("missing-key", {"action_type": 0, "x": 0, "y": 0}),
            ("extra-key", {**_action(ActionType.NOOP), "extra": 0}),
        ]
        invalid_records = []
        for name, action in invalid_actions:
            before = backend.structured_action_count
            try:
                env.step(action)
            except InvalidActionError as exc:
                rejected = True
                error = str(exc)
            else:
                rejected = False
                error = None
            invalid_records.append(
                {
                    "name": name,
                    "rejected": rejected,
                    "error": error,
                    "structured_action_count_before": before,
                    "structured_action_count_after": backend.structured_action_count,
                    "rejected_before_backend_execution": (
                        before == backend.structured_action_count
                    ),
                }
            )

        metadata = backend.integration_metadata()
        evidence = {
            "schema_version": 1,
            "validator": "real-space-smoke",
            "backend": "real-osworld-docker",
            "seed": 7,
            "task_id": info["task_id"],
            "valid_action_count": len(valid_actions),
            "observations": observation_records,
            "invalid_actions": invalid_records,
            "empty_submit": {
                "submission_count": len(submissions),
                "submitted_values_were_empty": (
                    len(submissions) == 1 and dict(submissions[0].values) == blank_values
                ),
                "rewards": reward_records,
                "passed": empty_submit_passed,
            },
            "backend_metadata": metadata,
            "summary": {
                "observations_contained": all(
                    record["contained"] for record in observation_records
                ),
                "invalid_inputs_rejected_before_backend": all(
                    record["rejected"] and record["rejected_before_backend_execution"]
                    for record in invalid_records
                ),
                "empty_submit_no_reward": empty_submit_passed,
                "provider_closed": False,
                "passed": False,
            },
        }
    finally:
        env.close()
        provider_closed = True

    evidence["summary"]["provider_closed"] = provider_closed
    evidence["summary"]["passed"] = all(
        evidence["summary"][key]
        for key in (
            "observations_contained",
            "invalid_inputs_rejected_before_backend",
            "empty_submit_no_reward",
            "provider_closed",
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guest-image",
        type=Path,
        default=Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        evidence = run(args.guest_image, args.output)
    except Exception as exc:  # noqa: BLE001 - CLI must surface integration failure
        print(f"Real OSWorld smoke failed: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(evidence["summary"], indent=2, sort_keys=True))
    return 0 if evidence["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
