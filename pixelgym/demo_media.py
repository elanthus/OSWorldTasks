"""Demo GIF and review-media helpers backed by stored validation evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.tasks.vendor_form.render import BOLD_FONT, REGULAR_FONT

DEMO_SCHEMA_VERSION = "pixelgym-demo-review-v1"
ACTION_NAMES = {0: "NOOP", 1: "CLICK", 2: "KEY"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD_FONT if bold else REGULAR_FONT), size)


def _frame(
    source: Image.Image,
    *,
    step: int,
    total_steps: int,
    action_name: str,
    reward: float,
    terminated: bool,
    size: tuple[int, int],
) -> Image.Image:
    width, screenshot_height = size
    screenshot = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    footer_height = 54
    canvas = Image.new("RGB", (width, screenshot_height + footer_height), (22, 26, 31))
    canvas.paste(screenshot, (0, 0))
    draw = ImageDraw.Draw(canvas)
    status = "success / terminated" if terminated else "continuing"
    reward_color = (91, 214, 138) if reward > 0 else (218, 222, 229)
    status_text = f"reward {reward:.1f} | {status}"
    status_font = _font(15, bold=True)
    status_box = draw.textbbox((0, 0), status_text, font=status_font)
    status_width = status_box[2] - status_box[0]
    draw.text(
        (18, screenshot_height + 9),
        f"Real OSWorld episode | step {step:03d}/{total_steps:03d} | {action_name}",
        font=_font(16, bold=True),
        fill=(245, 247, 250),
    )
    draw.text(
        (width - status_width - 18, screenshot_height + 10),
        status_text,
        font=status_font,
        fill=reward_color,
    )
    return canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def generate_episode_gif(
    *,
    repository_root: Path,
    evidence_path: Path,
    frames_directory: Path,
    output_path: Path,
    metadata_path: Path,
    screenshot_size: tuple[int, int] = (960, 540),
) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text())
    backend_metadata = evidence.get("backend_metadata")
    if (
        not isinstance(backend_metadata, dict)
        or backend_metadata.get("upstream_repository")
        != "https://github.com/xlang-ai/OSWorld-V2"
        or not str(backend_metadata.get("release", "")).startswith("osworld-v2-")
        or not str(backend_metadata.get("upstream_tag", "")).startswith("v")
    ):
        raise ValueError("demo evidence is not attributable to the pinned OSWorld backend")
    trace = evidence["trace"]
    if not trace:
        raise ValueError("real episode trace is empty")
    steps = [row["step"] for row in trace]
    if steps != list(range(1, len(trace) + 1)):
        raise ValueError("real episode trace steps are not contiguous")
    positive = [row for row in trace if row["reward"] > 0]
    if len(positive) != 1 or positive[0] is not trace[-1]:
        raise ValueError("demo requires exactly one positive reward on the final step")
    if trace[-1]["reward"] != 1.0 or trace[-1]["terminated"] is not True:
        raise ValueError("demo terminal record does not contain reward 1.0 and termination")
    source_paths = [frames_directory / "step-000.png"] + [
        frames_directory / f"step-{step:03d}.png" for step in steps
    ]
    missing = [path for path in source_paths if not path.is_file()]
    if missing:
        raise ValueError(f"demo source frame is missing: {missing[0]}")
    frames = [
        _frame(
            Image.open(source_paths[0]),
            step=0,
            total_steps=len(trace),
            action_name="RESET",
            reward=0.0,
            terminated=False,
            size=screenshot_size,
        )
    ]
    for source_path, row in zip(source_paths[1:], trace, strict=True):
        action_type = row["action"]["action_type"]
        if action_type not in ACTION_NAMES:
            raise ValueError(f"demo trace has unknown action type {action_type!r}")
        frames.append(
            _frame(
                Image.open(source_path),
                step=row["step"],
                total_steps=len(trace),
                action_name=ACTION_NAMES[action_type],
                reward=float(row["reward"]),
                terminated=row["terminated"],
                size=screenshot_size,
            )
        )
    durations_ms = [1_000] + [280] * (len(frames) - 2) + [2_000]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations_ms,
        loop=0,
        optimize=False,
        disposal=2,
    )
    source_hashes = {path.name: _sha256(path) for path in source_paths}
    metadata = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "source_evidence_path": evidence_path.relative_to(repository_root).as_posix(),
        "source_evidence_sha256": _sha256(evidence_path),
        "source_frame_count": len(source_paths),
        "source_frame_sha256": source_hashes,
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_sha256": _sha256(output_path),
        "output_width": frames[0].width,
        "output_height": frames[0].height,
        "duration_seconds": sum(durations_ms) / 1000,
        "action_count": len(trace),
        "positive_reward_count": len(positive),
        "terminal_reward": trace[-1]["reward"],
        "terminal_step": trace[-1]["step"],
        "synthetic_task_only": True,
        "credential_or_private_ui_review": "pending_human_review",
        "approved_for_public_readme": False,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata
