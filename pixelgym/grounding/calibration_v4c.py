"""Capture and validate the deterministic v4c longer-horizon state graph."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from pixelgym.grounding.capture import _candidate_record
from pixelgym.grounding.determinism import compare_png_directories
from pixelgym.grounding.overlays import proposal_match, render_overlay
from pixelgym.grounding.schema import DEVICE_SCALE_FACTOR, validate_candidate_set
from pixelgym.grounding.v4c_protocol import (
    V4C_CONDITIONS,
    V4C_EPISODES,
    V4C_HEIGHT,
    V4C_PROTOCOL_VERSION,
    V4C_SEEDS,
    V4C_WIDTH,
    episode_decisions,
    episode_max_actions,
    v4c_task_id,
    validate_v4c_protocol,
)
from pixelgym.grounding.v4c_server import V4C_READY_SELECTOR, local_v4c_server
from pixelgym.serialization import canonical_json_text

V4C_CAPTURE_SCHEMA_VERSION = "pixelgym-grounding-v4c-capture-v1"
V4C_STATE_SCHEMA_VERSION = "pixelgym-grounding-v4c-state-v1"
V4C_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-v4c-candidates-v1"
V4C_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-v4c-overlay-v1"
V4C_MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-v4c-manifest-v1"

_V4C_CAPTURE_SOURCE_PATHS = (
    "pixelgym/grounding/calibration_v4c.py",
    "pixelgym/grounding/v4c_protocol.py",
    "pixelgym/grounding/v4c_server.py",
    "pixelgym/grounding/v4c_app/static/app.js",
    "pixelgym/grounding/v4c_app/static/index.html",
    "pixelgym/grounding/v4c_app/static/style.css",
    "pixelgym/grounding/capture.py",
    "pixelgym/grounding/determinism.py",
    "pixelgym/grounding/overlays.py",
    "pixelgym/tasks/vendor_form/app/static/fonts/DejaVuSans.ttf",
    "pixelgym/tasks/vendor_form/app/static/fonts/DejaVuSans-Bold.ttf",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def v4c_capture_source_hashes(repository_root: Path) -> dict[str, str]:
    return {
        relative: _sha256((repository_root / relative).read_bytes())
        for relative in _V4C_CAPTURE_SOURCE_PATHS
    }


def state_id(seed: int, stage: int, pinned: bool, recovery: bool) -> str:
    suffix = "recovery" if recovery else "main"
    return f"v4c-{seed}-s{stage}-p{int(pinned)}-{suffix}"


def reachable_states() -> list[tuple[int, int, bool, bool]]:
    states: list[tuple[int, int, bool, bool]] = []
    for episode in V4C_EPISODES:
        seed = episode["seed"]
        commit = episode["commit_stage"]
        decisions = len(episode["stages"])
        for stage in range(decisions):
            pin_values = (False,) if stage <= commit else (False, True)
            for pinned in pin_values:
                for recovery in (False, True):
                    states.append((seed, stage, pinned, recovery))
        states.extend((seed, decisions, pinned, False) for pinned in (False, True))
    return states


def actionable_states() -> list[tuple[int, int, bool, bool]]:
    return [
        (seed, stage, pinned, recovery)
        for seed, stage, pinned, recovery in reachable_states()
        if stage < episode_decisions(seed)
    ]


_CANDIDATE_SCRIPT = """
() => ({
  css_width: window.innerWidth,
  css_height: window.innerHeight,
  device_scale_factor: window.devicePixelRatio,
  candidates: [...document.querySelectorAll("button.option")].map((element) => {
    const rect = element.getBoundingClientRect();
    return {
      semantic_id: element.dataset.semanticId,
      element_type: "button",
      visible_label: element.textContent,
      css_bbox: [rect.x, rect.y, rect.right, rect.bottom],
    };
  }),
})
"""


def _capture_pass(
    repository_root: Path, image_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - opt-in browser dependency
        raise RuntimeError('v4c capture requires `pip install -e ".[dev]"`') from exc

    from pixelgym.tasks.vendor_form.browser_contract import BROWSER_ARGS

    states: list[dict[str, Any]] = []
    candidate_records: list[dict[str, Any]] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    browser_version = "unknown"
    with local_v4c_server() as base_url, sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(BROWSER_ARGS)}
        if executable.is_file():
            launch_options["executable_path"] = str(executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:  # pragma: no cover - host setup
            raise RuntimeError("v4c capture requires the Playwright Chromium binary") from exc
        browser_version = browser.version
        context = browser.new_context(
            viewport={"width": V4C_WIDTH, "height": V4C_HEIGHT},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
        )
        page = context.new_page()
        try:
            for seed, stage, pinned, recovery in reachable_states():
                query = f"seed={seed}&stage={stage}&pinned={int(pinned)}&recovery={int(recovery)}"
                page.goto(f"{base_url}/?{query}", wait_until="networkidle")
                page.locator(V4C_READY_SELECTOR).wait_for(state="attached")
                page.evaluate("() => document.fonts.ready")
                instrumentation = page.evaluate(_CANDIDATE_SCRIPT)
                if (
                    instrumentation["css_width"] != V4C_WIDTH
                    or instrumentation["css_height"] != V4C_HEIGHT
                    or instrumentation["device_scale_factor"] != DEVICE_SCALE_FACTOR
                ):
                    raise RuntimeError("browser viewport does not match v4c protocol")
                identifier = state_id(seed, stage, pinned, recovery)
                image_path = image_dir / f"{identifier}.png"
                image_bytes = page.screenshot(type="png", animations="disabled")
                image_path.write_bytes(image_bytes)
                with Image.open(image_path) as decoded:
                    width, height = decoded.size
                    pixel_sha256 = _sha256(decoded.convert("RGB").tobytes())
                candidates = [
                    _candidate_record(row, screen_width=width, screen_height=height)
                    for row in instrumentation["candidates"]
                ]
                if stage < episode_decisions(seed):
                    validate_candidate_set(candidates, width=width, height=height)
                elif candidates:
                    raise ValueError("terminal v4c states must contain no actions")
                states.append(
                    {
                        "schema_version": V4C_STATE_SCHEMA_VERSION,
                        "protocol_version": V4C_PROTOCOL_VERSION,
                        "state_id": identifier,
                        "seed": seed,
                        "stage": stage,
                        "pinned": pinned,
                        "recovery": recovery,
                        "image_path": image_path.relative_to(repository_root).as_posix(),
                        "image_sha256": _sha256(image_bytes),
                        "pixel_sha256": pixel_sha256,
                        "screen_width": width,
                        "screen_height": height,
                    }
                )
                candidate_records.append(
                    {
                        "schema_version": V4C_CANDIDATE_SCHEMA_VERSION,
                        "protocol_version": V4C_PROTOCOL_VERSION,
                        "state_id": identifier,
                        "candidates": candidates,
                    }
                )
        finally:
            context.close()
            browser.close()
    return states, candidate_records, browser_version


def _generate_overlays(
    repository_root: Path,
    states: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_state = {row["state_id"]: row["candidates"] for row in candidates}
    episode_by_seed = {episode["seed"]: episode for episode in V4C_EPISODES}
    marked_dir = repository_root / "artifacts" / "grounding-v4c-pilot" / "images" / "marks"
    marked_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for state in states:
        if state["stage"] >= episode_decisions(state["seed"]):
            continue
        raw_path = repository_root / state["image_path"]
        with Image.open(raw_path) as raw:
            marked, marks = render_overlay(raw, by_state[state["state_id"]])
        marked_path = marked_dir / f"{state['state_id']}.png"
        marked.save(marked_path, format="PNG", optimize=False, compress_level=9)
        target = episode_by_seed[state["seed"]]["stages"][state["stage"]]["target"]
        covered, _ = proposal_match(target, marks)
        if not covered:
            raise ValueError("v4c target missing from target-independent proposals")
        overlays.append(
            {
                "schema_version": V4C_OVERLAY_SCHEMA_VERSION,
                "protocol_version": V4C_PROTOCOL_VERSION,
                "state_id": state["state_id"],
                "raw_image_sha256": state["image_sha256"],
                "marked_image_path": marked_path.relative_to(repository_root).as_posix(),
                "marked_image_sha256": _sha256(marked_path.read_bytes()),
                "marks": marks,
                "proposal_coverage": True,
            }
        )
    return overlays


def validate_v4c_capture(
    states: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> dict[str, Any]:
    protocol = validate_v4c_protocol()
    expected = {state_id(*row) for row in reachable_states()}
    actual = [row.get("state_id") for row in states]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("v4c state graph is incomplete or contains duplicates")
    candidate_ids = [row.get("state_id") for row in candidates]
    if len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) != expected:
        raise ValueError("v4c candidate records do not match the state graph")
    action_states = {state_id(*row) for row in actionable_states()}
    overlay_ids = [row.get("state_id") for row in overlays]
    if len(overlay_ids) != len(set(overlay_ids)) or set(overlay_ids) != action_states:
        raise ValueError("v4c overlays do not cover every actionable state")
    if any(_contains_target_key(row) for row in candidates + overlays):
        raise ValueError("v4c candidate and overlay records must not leak targets")
    if any(row.get("proposal_coverage") is not True for row in overlays):
        raise ValueError("v4c proposal coverage must be 100%")
    family_counts = Counter(episode["family"] for episode in V4C_EPISODES)
    return {
        **protocol,
        "reachable_state_count": len(states),
        "actionable_state_count": len(overlays),
        "proposal_covered_state_count": len(overlays),
        "family_counts": dict(sorted(family_counts.items())),
    }


def _contains_target_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(
            {"target", "target_id", "skip", "commit_stage", "consumer_stage"} & set(value)
        ) or any(_contains_target_key(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_target_key(child) for child in value)
    return False


def capture_v4c_pilot(repository_root: Path) -> dict[str, Any]:
    artifact_root = repository_root / "artifacts"
    pilot_root = artifact_root / "grounding-v4c-pilot"
    raw_dir = pilot_root / "images" / "raw"
    repeat_dir = pilot_root / "images" / "raw-repeat"
    states, candidates, browser_version = _capture_pass(repository_root, raw_dir)
    _capture_pass(repository_root, repeat_dir)
    repeatability = compare_png_directories(
        raw_dir,
        repeat_dir,
        reference_label="v4c-pass-1",
        candidate_label="v4c-pass-2",
    )
    if (
        repeatability["file_count"] != len(reachable_states())
        or repeatability["differing_file_count"] != 0
        or repeatability["byte_identical_file_count"] != repeatability["file_count"]
    ):
        raise RuntimeError("v4c capture was not bitwise repeatable")
    overlays = _generate_overlays(repository_root, states, candidates)
    summary = validate_v4c_capture(states, candidates, overlays)

    outputs = {
        "episodes": artifact_root / "grounding-v4c-pilot-episodes.json",
        "states": artifact_root / "grounding-v4c-pilot-states.jsonl",
        "candidates": artifact_root / "grounding-v4c-pilot-candidates.jsonl",
        "overlays": artifact_root / "grounding-v4c-pilot-overlays.jsonl",
        "capture": artifact_root / "grounding-v4c-pilot-capture.json",
    }
    outputs["episodes"].write_text(
        json.dumps(
            {
                "schema_version": "pixelgym-grounding-v4c-episodes-v1",
                "protocol_version": V4C_PROTOCOL_VERSION,
                "episodes": V4C_EPISODES,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    for key, rows in (("states", states), ("candidates", candidates), ("overlays", overlays)):
        outputs[key].write_text(
            "".join(canonical_json_text(row) + "\n" for row in rows), encoding="utf-8"
        )
    capture = {
        "schema_version": V4C_CAPTURE_SCHEMA_VERSION,
        "protocol_version": V4C_PROTOCOL_VERSION,
        "browser_engine": "chromium",
        "browser_version": browser_version,
        **summary,
        "repeatability": repeatability,
        "source_sha256": v4c_capture_source_hashes(repository_root),
    }
    outputs["capture"].write_text(
        json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": V4C_MANIFEST_SCHEMA_VERSION,
        "protocol_version": V4C_PROTOCOL_VERSION,
        "status": "captured_evaluation_not_run",
        "model": "gpt-5.6-luna",
        "parameters": {"reasoning_effort": "low", "temperature": None},
        "seeds": list(V4C_SEEDS),
        "conditions": list(V4C_CONDITIONS),
        "max_actions_per_condition": {str(seed): episode_max_actions(seed) for seed in V4C_SEEDS},
        "approved_call_cap": summary["call_cap"],
        "model_calls_performed": 0,
        "decision_history": [],
        "family_counts": summary["family_counts"],
        "task_ids": {str(seed): v4c_task_id(seed) for seed in V4C_SEEDS},
        "outputs": {
            name: {
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
            for name, path in outputs.items()
        },
    }
    manifest_path = artifact_root / "grounding-v4c-pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
