"""Generate, check, or record the seed-7 OSWorld golden trajectory.

The committed fixture is literal public action data.  It deliberately uses
Tab/arrow navigation after task setup focuses the page, so it does not depend
on browser-toolbar offsets or privileged element geometry.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixelgym.actions import KEY_ALLOWLIST, KEY_ALLOWLIST_VERSION, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import TaskSpec
from pixelgym.tasks.vendor_form import generator

DEFAULT_FIXTURE = Path("tests/integration/fixtures/osworld_golden_trajectory_seed7.json")
DEFAULT_OUTPUT_DIR = Path("artifacts/day-2/real-golden")
_KEY_INDEX = {value: index for index, value in enumerate(KEY_ALLOWLIST)}


def _key(value: str) -> dict[str, int]:
    return {
        "action_type": int(ActionType.KEY),
        "x": 0,
        "y": 0,
        "key": _KEY_INDEX[value],
    }


def _noop() -> dict[str, int]:
    return {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}


def build_candidate(seed: int = 7) -> dict[str, Any]:
    record = generator.generate_task(seed)
    task_body = {key: value for key, value in record.items() if key != "task_id"}
    fields = record["fields"]
    actions = [_noop(), _key("Tab")]
    for field in ("company_name", "contact_email", "contact_phone", "tax_id"):
        actions.extend(_key(character) for character in fields[field])
        actions.append(_key("Tab"))

    country_index = record["options"]["country"].index(fields["country"])
    actions.extend(_key("ArrowDown") for _ in range(country_index + 1))
    actions.append(_key("Tab"))

    payment_index = record["options"]["payment_terms"].index(fields["payment_terms"])
    # With no radio selected, both the fake model and Chromium treat ArrowLeft
    # from the group's initial focus as selecting the final option.  Advancing
    # from there avoids relying on whether merely tabbing into an unchecked
    # radio group changes its value.
    actions.append(_key("ArrowLeft"))
    actions.extend(
        _key("ArrowRight")
        for _ in range((payment_index + 1) % len(record["options"]["payment_terms"]))
    )
    actions.append(_key("Tab"))

    if fields["expedited_onboarding"]:
        actions.append(_key(" "))
    actions.extend([_key("Tab"), _key("Enter")])

    timeline = replay_fake(actions, seed=seed)
    return {
        "schema_version": 1,
        "note": (
            "Integration-only frozen public-action trajectory. It contains no expected values or "
            "bounding boxes and is replayed without consulting the generator."
        ),
        "seed": seed,
        "task_id": record["task_id"],
        "task_spec_sha256": hashlib.sha256(
            generator.canonical_json(task_body).encode("utf-8")
        ).hexdigest(),
        "key_allowlist_version": KEY_ALLOWLIST_VERSION,
        "navigation": "page-focused Tab order; no integration coordinates",
        "action_count": len(actions),
        "actions": actions,
        "fake_backend_timeline": timeline,
    }


def replay_fake(actions: list[dict[str, int]], *, seed: int) -> list[dict[str, Any]]:
    env = PixelGuiEnv(FakeBackend())
    env.reset(seed=seed)
    timeline = []
    try:
        for step, action in enumerate(actions, start=1):
            _obs, reward, terminated, truncated, _info = env.step(action)
            timeline.append(
                {
                    "step": step,
                    "action_type": action["action_type"],
                    "reward": reward,
                    "terminated": terminated,
                    "truncated": truncated,
                }
            )
    finally:
        env.close()
    return timeline


def verify_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    timeline = replay_fake(fixture["actions"], seed=fixture["seed"])
    expected_rewards = [0.0] * (len(timeline) - 1) + [1.0]
    if timeline != fixture["fake_backend_timeline"]:
        raise ValueError("fixture timeline drifted from blind fake-backend replay")
    if [item["reward"] for item in timeline] != expected_rewards:
        raise ValueError("fixture reward timeline is not zeros followed by exactly one")
    if not timeline[-1]["terminated"] or any(item["truncated"] for item in timeline):
        raise ValueError("fixture must terminate only on its final action and never truncate")
    return fixture


def _contact_sheet(paths: list[Path], output: Path) -> None:
    columns = 5
    thumb_width, thumb_height = 384, 216
    label_height = 20
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_width, rows * (thumb_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as frame:
            thumb = frame.convert("RGB")
            thumb.thumbnail((thumb_width, thumb_height))
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + label_height)
        sheet.paste(thumb, (x, y))
        draw.text((x + 4, y + thumb_height + 3), f"step {index}", fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def record_real(fixture_path: Path, guest_image: Path, output_dir: Path) -> dict[str, Any]:
    fixture = verify_fixture(fixture_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=guest_image))
    env = PixelGuiEnv(backend)
    frame_paths: list[Path] = []
    trace = []
    provider_closed = False
    try:
        observation, info = env.reset(seed=fixture["seed"])
        initial_path = frames_dir / "step-000.png"
        Image.fromarray(observation).save(initial_path)
        frame_paths.append(initial_path)
        task = TaskSpec.from_generated(
            generator.generate_task(fixture["seed"]),
            instruction="Fill out the form exactly as shown on the request card, then submit.",
            app_url=backend.app_url,
            max_episode_steps=200,
        )
        for step, action in enumerate(fixture["actions"], start=1):
            observation, reward, terminated, truncated, step_info = env.step(action)
            frame_path = frames_dir / f"step-{step:03d}.png"
            Image.fromarray(observation).save(frame_path)
            frame_paths.append(frame_path)
            diagnostics = dataclasses.asdict(evaluate(task, backend.last_submissions))
            trace.append(
                {
                    "step": step,
                    "action": action,
                    "reward": reward,
                    "terminated": terminated,
                    "truncated": truncated,
                    "info": step_info,
                    "evaluator": diagnostics,
                    "screenshot_path": str(frame_path),
                }
            )
        metadata = backend.integration_metadata()
    finally:
        env.close()
        provider_closed = True

    rewards = [item["reward"] for item in trace]
    passed = (
        rewards == [0.0] * (len(trace) - 1) + [1.0]
        and trace[-1]["terminated"]
        and not any(item["truncated"] for item in trace)
        and provider_closed
    )
    contact_sheet = output_dir / "contact-sheet.png"
    _contact_sheet(frame_paths, contact_sheet)
    evidence = {
        "schema_version": 1,
        "validator": "real-golden-episode",
        "seed": fixture["seed"],
        "task_id": info["task_id"],
        "fixture_path": str(fixture_path),
        "action_count": len(trace),
        "trace": trace,
        "contact_sheet_path": str(contact_sheet),
        "backend_metadata": metadata,
        "real_reward_timing_subset": [
            {
                "name": "correct-fields-without-submit",
                "step": trace[-2]["step"],
                "reward": trace[-2]["reward"],
                "terminated": trace[-2]["terminated"],
                "truncated": trace[-2]["truncated"],
                "passed": (
                    trace[-2]["reward"] == 0.0
                    and not trace[-2]["terminated"]
                    and not trace[-2]["truncated"]
                ),
            },
            {
                "name": "complete-golden-trajectory",
                "step": trace[-1]["step"],
                "reward": trace[-1]["reward"],
                "terminated": trace[-1]["terminated"],
                "truncated": trace[-1]["truncated"],
                "passed": (
                    trace[-1]["reward"] == 1.0
                    and trace[-1]["terminated"]
                    and not trace[-1]["truncated"]
                ),
            },
        ],
        "summary": {
            "positive_reward_count": sum(reward > 0 for reward in rewards),
            "first_positive_reward_step": next(
                (index for index, reward in enumerate(rewards, start=1) if reward > 0), None
            ),
            "terminal_step": next((item["step"] for item in trace if item["terminated"]), None),
            "provider_closed": provider_closed,
            "passed": passed,
        },
    }
    evidence_path = output_dir.parent / "raw" / "real-golden-episode.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--seed", type=int, default=7)
    generate_parser.add_argument("--output", type=Path, default=DEFAULT_FIXTURE)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    record_parser.add_argument(
        "--guest-image",
        type=Path,
        default=Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2"),
    )
    record_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    if args.command == "generate":
        candidate = build_candidate(args.seed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
        print(args.output)
    elif args.command == "check":
        fixture = verify_fixture(args.fixture)
        print(
            json.dumps(
                {"fixture": str(args.fixture), "action_count": fixture["action_count"], "ok": True},
                sort_keys=True,
            )
        )
    else:
        evidence = record_real(args.fixture, args.guest_image, args.output_dir)
        print(json.dumps(evidence["summary"], indent=2, sort_keys=True))
        return 0 if evidence["summary"]["passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
