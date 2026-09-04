"""Browser evidence for submission and guest navigation-surface boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image

from pixelgym.evaluator import evaluate
from pixelgym.grounding.schema import CSS_HEIGHT, CSS_WIDTH, DEVICE_SCALE_FACTOR
from pixelgym.task_spec import Submission, TaskSpec
from pixelgym.tasks.vendor_form.browser_contract import (
    READY_SELECTOR,
    ChromiumLaunchPath,
    build_chromium_argv,
    chromium_renderer_contract,
    local_vendor_form_server,
)
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE

BROWSER_BOUNDARY_SCHEMA_VERSION = "pixelgym-browser-boundary-v2"
BROWSER_BOUNDARY_VALIDATOR = "vendor-form-browser-boundary"
GUEST_BROWSER_BOUNDARY_SCHEMA_VERSION = "pixelgym-guest-browser-boundary-v1"
GUEST_BROWSER_BOUNDARY_VALIDATOR = "vendor-form-guest-navigation-boundary"

SOURCE_PATHS = (
    "pixelgym/validation/browser_boundary.py",
    "pixelgym/tasks/vendor_form/app/static/app.js",
    "pixelgym/tasks/vendor_form/app/server.py",
    "pixelgym/tasks/vendor_form/browser_contract.py",
    "pixelgym/evaluator.py",
    "pixelgym/env.py",
)

GUEST_SOURCE_PATHS = (
    "pixelgym/validation/browser_boundary.py",
    "pixelgym/tasks/vendor_form/browser_contract.py",
    "pixelgym/tasks/vendor_form/osworld_task.py",
    "pixelgym/backends/osworld.py",
)

_EMPTY_VALUES = {
    "company_name": "",
    "contact_email": "",
    "contact_phone": "",
    "tax_id": "",
    "country": "",
    "payment_terms": "",
    "expedited_onboarding": False,
}

_REQUIRED_CHECKS = {
    "submit_response_ok",
    "submit_response_body_exact",
    "visible_validation_state_settled",
    "one_privileged_submission_recorded",
    "privileged_submission_exact",
    "evaluator_observed_submission",
    "evaluator_matched_task_identity",
    "evaluator_rejected_incomplete_submission",
    "derived_environment_reward_zero",
    "evaluator_mismatches_exact",
}

_GUEST_REQUIRED_CHECKS = {
    "active_window_identified",
    "active_window_is_chromium",
    "active_window_fills_observation",
    "active_window_uses_protected_presentation",
    "observation_shape_exact",
    "task_app_reaches_top_edge",
    "renderer_contract_matches_guest_launch",
    "provider_closed",
}

_TOP_EDGE_ANCHORS = (
    (0, 0, (238, 241, 246)),
    (511, 0, (199, 205, 214)),
    (512, 0, (255, 255, 255)),
    (1023, 0, (255, 255, 255)),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(repository_root: Path, paths: tuple[str, ...] = SOURCE_PATHS) -> dict[str, str]:
    """Hash every implementation source that the browser-boundary result depends on."""
    return {relative: _sha256(repository_root / relative) for relative in paths}


def _json_request(url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError("browser boundary returned a non-object JSON response")
    return cast(dict[str, Any], value)


def browser_boundary_evidence_passed(evidence: dict[str, Any] | None) -> bool:
    """Validate the stored evidence shape before the reward audit relies on it."""
    if not isinstance(evidence, dict):
        return False
    if evidence.get("schema_version") != BROWSER_BOUNDARY_SCHEMA_VERSION:
        return False
    if evidence.get("validator") != BROWSER_BOUNDARY_VALIDATOR:
        return False
    source = evidence.get("source_sha256")
    if not isinstance(source, dict) or set(source) != set(SOURCE_PATHS):
        return False
    if any(
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for digest in source.values()
    ):
        return False
    browser = evidence.get("browser")
    if (
        not isinstance(browser, dict)
        or browser.get("engine") != "chromium"
        or not isinstance(browser.get("version"), str)
        or not browser["version"]
        or browser.get("renderer_contract") != chromium_renderer_contract()
        or browser.get("args")
        != list(build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)[1:])
    ):
        return False
    task_id = evidence.get("task_id")
    seed = evidence.get("seed")
    boundary = evidence.get("boundary")
    if not isinstance(task_id, str) or not task_id or type(seed) is not int:
        return False
    if not isinstance(boundary, dict):
        return False
    submission = boundary.get("submission")
    evaluation = boundary.get("evaluation")
    if boundary.get("submit_response_status") != 200:
        return False
    if boundary.get("submit_response_body") != {"submission_number": 1}:
        return False
    if boundary.get("visible_status") != INCOMPLETE_SUBMISSION_MESSAGE:
        return False
    if boundary.get("submission_count") != 1 or not isinstance(submission, dict):
        return False
    if submission != {
        "task_id": task_id,
        "seed": seed,
        "values": _EMPTY_VALUES,
        "submitted_at_step": 1,
        "final": True,
    }:
        return False
    if not isinstance(evaluation, dict):
        return False
    if (
        evaluation.get("submitted") is not True
        or evaluation.get("task_id_matches") is not True
        or evaluation.get("success") is not False
        or evaluation.get("derived_environment_reward") != 0.0
        or not isinstance(evaluation.get("mismatched_fields"), list)
        or not evaluation["mismatched_fields"]
    ):
        return False
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return False
    checks_by_name = {
        row.get("name"): row.get("passed")
        for row in checks
        if isinstance(row, dict) and set(row) == {"name", "passed"}
    }
    if set(checks_by_name) != _REQUIRED_CHECKS or not all(
        value is True for value in checks_by_name.values()
    ):
        return False
    summary = evidence.get("summary")
    return summary == {
        "check_count": len(_REQUIRED_CHECKS),
        "passed_count": len(_REQUIRED_CHECKS),
        "failed_count": 0,
        "passed": True,
    }


def browser_boundary_source_hashes_match(
    evidence: dict[str, Any] | None, repository_root: Path
) -> bool:
    """Whether stored evidence names the exact implementation currently checked out."""
    return isinstance(evidence, dict) and evidence.get("source_sha256") == source_hashes(
        repository_root
    )


def inspect_navigation_surface(
    window_state: dict[str, Any], screenshot: np.ndarray[Any, Any]
) -> dict[str, Any]:
    """Evaluate structural and pixel evidence without launching a browser."""

    active_id = window_state.get("active_window_id")
    windows = window_state.get("windows")
    active = None

    def normalized_window_id(value: object) -> int | None:
        if not isinstance(value, str):
            return None
        try:
            return int(value, 16)
        except ValueError:
            return None

    normalized_active_id = normalized_window_id(active_id)
    if isinstance(active_id, str) and isinstance(windows, list):
        matches = [
            item
            for item in windows
            if isinstance(item, dict)
            and normalized_window_id(item.get("id")) == normalized_active_id
        ]
        if len(matches) == 1:
            active = matches[0]
    properties = window_state.get("active_window_properties")
    chromium_class = active.get("class", "") if isinstance(active, dict) else ""
    fullscreen = (
        isinstance(properties, str) and "_NET_WM_STATE_FULLSCREEN" in properties
    )
    expected_bounds = {
        "x": 0,
        "y": 0,
        "width": CSS_WIDTH,
        "height": CSS_HEIGHT,
    }
    bounds = (
        {name: active.get(name) for name in expected_bounds}
        if isinstance(active, dict)
        else None
    )
    shape_exact = screenshot.shape == (CSS_HEIGHT, CSS_WIDTH, 3)
    anchors = []
    for x, y, expected in _TOP_EDGE_ANCHORS:
        observed = (
            screenshot[y, x].tolist()
            if screenshot.ndim == 3
            and screenshot.shape[0] > y
            and screenshot.shape[1] > x
            and screenshot.shape[2] == 3
            else None
        )
        anchors.append(
            {
                "xy": [x, y],
                "expected_rgb": list(expected),
                "observed_rgb": observed,
                "matched": observed == list(expected),
            }
        )
    check_values = {
        "active_window_identified": active is not None,
        "active_window_is_chromium": (
            isinstance(chromium_class, str) and "chrome" in chromium_class.lower()
        ),
        "active_window_fills_observation": bounds == expected_bounds,
        "observation_shape_exact": shape_exact,
        "task_app_reaches_top_edge": shape_exact and all(row["matched"] for row in anchors),
    }
    return {
        "active_window_id": active_id,
        "active_window": active,
        "active_window_properties": properties,
        "active_window_fullscreen": fullscreen,
        "windows": windows,
        "expected_bounds": expected_bounds,
        "observation_shape": list(screenshot.shape),
        "observation_dtype": str(screenshot.dtype),
        "top_edge_anchors": anchors,
        "checks": check_values,
    }


def guest_browser_boundary_evidence_passed(evidence: dict[str, Any] | None) -> bool:
    """Validate stored real-guest evidence before the reward audit relies on it."""

    if not isinstance(evidence, dict):
        return False
    if evidence.get("schema_version") != GUEST_BROWSER_BOUNDARY_SCHEMA_VERSION:
        return False
    if evidence.get("validator") != GUEST_BROWSER_BOUNDARY_VALIDATOR:
        return False
    source = evidence.get("source_sha256")
    if not isinstance(source, dict) or set(source) != set(GUEST_SOURCE_PATHS):
        return False
    launch = evidence.get("browser_launch")
    if (
        not isinstance(launch, dict)
        or launch.get("presentation_mode") != "app"
        or launch.get("presentation_mode_fallback_from") != "kiosk"
        or not isinstance(launch.get("presentation_mode_reason"), str)
        or not launch["presentation_mode_reason"]
    ):
        return False
    renderer = launch.get("renderer_contract")
    if renderer != chromium_renderer_contract():
        return False
    if launch.get("effective_argv") != list(
        build_chromium_argv(ChromiumLaunchPath.OSWORLD_GUEST)
    ):
        return False
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return False
    checks_by_name = {
        row.get("name"): row.get("passed")
        for row in checks
        if isinstance(row, dict) and set(row) == {"name", "passed"}
    }
    if set(checks_by_name) != _GUEST_REQUIRED_CHECKS:
        return False
    summary = evidence.get("summary")
    return all(value is True for value in checks_by_name.values()) and summary == {
        "check_count": len(_GUEST_REQUIRED_CHECKS),
        "passed_count": len(_GUEST_REQUIRED_CHECKS),
        "failed_count": 0,
        "passed": True,
    }


def guest_browser_boundary_source_hashes_match(
    evidence: dict[str, Any] | None, repository_root: Path
) -> bool:
    return isinstance(evidence, dict) and evidence.get("source_sha256") == source_hashes(
        repository_root, GUEST_SOURCE_PATHS
    )


def validate_guest_browser_boundary(
    repository_root: Path,
    *,
    guest_image_path: Path,
    screenshot_path: Path,
    seed: int = 7,
) -> dict[str, Any]:
    """Capture a real 1024x768 guest frame and trusted window-mode evidence."""

    from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
    from pixelgym.env import PixelGuiEnv

    backend = OSWorldBackend(
        OSWorldBackendConfig(
            guest_image_path=guest_image_path,
            width=CSS_WIDTH,
            height=CSS_HEIGHT,
        )
    )
    env = PixelGuiEnv(backend)
    error: dict[str, str] | None = None
    evidence: dict[str, Any] = {
        "schema_version": GUEST_BROWSER_BOUNDARY_SCHEMA_VERSION,
        "validator": GUEST_BROWSER_BOUNDARY_VALIDATOR,
        "captured_at": datetime.now(UTC).isoformat(),
        "source_sha256": source_hashes(repository_root, GUEST_SOURCE_PATHS),
        "seed": seed,
    }
    check_values: dict[str, bool] = {}
    try:
        screenshot, info = env.reset(seed=seed)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(screenshot).save(screenshot_path)
        navigation = inspect_navigation_surface(backend.read_browser_window_state(), screenshot)
        metadata = backend.integration_metadata()
        launch = metadata.get("browser_launch")
        check_values.update(cast(dict[str, bool], navigation.pop("checks")))
        check_values["renderer_contract_matches_guest_launch"] = (
            isinstance(launch, dict)
            and launch.get("renderer_contract") == chromium_renderer_contract()
        )
        effective_argv = launch.get("effective_argv") if isinstance(launch, dict) else None
        check_values["active_window_uses_protected_presentation"] = (
            isinstance(effective_argv, list)
            and "--app=http://127.0.0.1:3000/" in effective_argv
            and navigation.get("active_window_fullscreen") is True
        )
        evidence.update(
            {
                "task_id": info["task_id"],
                "screenshot_path": screenshot_path.as_posix(),
                "browser_launch": launch,
                "backend_metadata": metadata,
                "navigation_surface": navigation,
            }
        )
    except BaseException as exc:  # noqa: BLE001 - preserve real-provider failures in evidence
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            env.close()
        except BaseException as exc:  # noqa: BLE001 - cleanup result is part of raw evidence
            check_values["provider_closed"] = False
            evidence["cleanup_error"] = {"type": type(exc).__name__, "message": str(exc)}
        else:
            check_values["provider_closed"] = True

    if error is not None:
        evidence["error"] = error
    for name in _GUEST_REQUIRED_CHECKS:
        check_values.setdefault(name, False)
    checks = [{"name": name, "passed": check_values[name]} for name in sorted(check_values)]
    passed_count = sum(check_values.values())
    evidence["checks"] = checks
    evidence["summary"] = {
        "check_count": len(checks),
        "passed_count": passed_count,
        "failed_count": len(checks) - passed_count,
        "passed": passed_count == len(checks),
    }
    return evidence


def validate_browser_boundary(repository_root: Path, *, seed: int = 7) -> dict[str, Any]:
    """Exercise the real JavaScript POST path and return structured privileged evidence."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional browser tooling
        raise RuntimeError('browser validation requires `pip install -e ".[dev]"`') from exc

    with local_vendor_form_server() as base_url, sync_playwright() as playwright:
        reset = _json_request(f"{base_url}/api/reset", payload={"seed": seed})
        chromium_executable = Path(playwright.chromium.executable_path)
        playwright_argv = build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)
        launch_options: dict[str, Any] = {"headless": True, "args": list(playwright_argv[1:])}
        if chromium_executable.is_file():
            launch_options["executable_path"] = str(chromium_executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:  # pragma: no cover - only without installed Chromium
            raise RuntimeError(
                "browser validation requires Playwright Chromium; run "
                "`python -m playwright install chromium`"
            ) from exc
        context = browser.new_context(
            viewport={"width": CSS_WIDTH, "height": CSS_HEIGHT},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
        )
        page = context.new_page()
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator(READY_SELECTOR).wait_for(state="attached")
            with page.expect_response(
                lambda response: response.url.endswith("/api/submit")
            ) as submission_response:
                page.locator("#submit-button").click()
            response = submission_response.value
            response_body = response.json()
            visible_status = page.locator("#submit-status").inner_text()
            state = _json_request(f"{base_url}/api/state")
        finally:
            context.close()
            browser.close()

    submissions = state["submissions"]
    task_record = state["task"]
    task = TaskSpec.from_generated(
        task_record,
        instruction="Fill out the form exactly as shown on the request card, then submit.",
        app_url="browser-validation://vendor-form",
        max_episode_steps=200,
    )
    evaluation = evaluate(task, [Submission.from_record(record) for record in submissions])
    derived_reward = 1.0 if evaluation.success else 0.0
    expected_submission = {
        "task_id": task.task_id,
        "seed": seed,
        "values": _EMPTY_VALUES,
        "submitted_at_step": 1,
        "final": True,
    }
    expected_mismatches = sorted(
        name
        for name, expected in task.expected_fields.items()
        if type(_EMPTY_VALUES[name]) is not type(expected) or _EMPTY_VALUES[name] != expected
    )
    check_values = {
        "submit_response_ok": response.ok and response.status == 200,
        "submit_response_body_exact": response_body == {"submission_number": 1},
        "visible_validation_state_settled": visible_status == INCOMPLETE_SUBMISSION_MESSAGE,
        "one_privileged_submission_recorded": len(submissions) == 1,
        "privileged_submission_exact": submissions == [expected_submission],
        "evaluator_observed_submission": evaluation.submitted is True,
        "evaluator_matched_task_identity": evaluation.task_id_matches is True,
        "evaluator_rejected_incomplete_submission": evaluation.success is False,
        "derived_environment_reward_zero": derived_reward == 0.0,
        "evaluator_mismatches_exact": list(evaluation.mismatched_fields) == expected_mismatches,
    }
    checks = [{"name": name, "passed": passed} for name, passed in check_values.items()]
    passed_count = sum(row["passed"] for row in checks)
    return {
        "schema_version": BROWSER_BOUNDARY_SCHEMA_VERSION,
        "validator": BROWSER_BOUNDARY_VALIDATOR,
        "source_sha256": source_hashes(repository_root),
        "browser": {
            "engine": "chromium",
            "version": browser.version,
            "args": list(playwright_argv[1:]),
            "renderer_contract": chromium_renderer_contract(),
            "viewport": [CSS_WIDTH, CSS_HEIGHT],
            "device_scale_factor": DEVICE_SCALE_FACTOR,
        },
        "seed": seed,
        "task_id": reset["task_id"],
        "boundary": {
            "submit_response_status": response.status,
            "submit_response_body": response_body,
            "visible_status": visible_status,
            "submission_count": len(submissions),
            "submission": submissions[0] if len(submissions) == 1 else None,
            "evaluation": {
                "submitted": evaluation.submitted,
                "task_id_matches": evaluation.task_id_matches,
                "success": evaluation.success,
                "score": evaluation.score,
                "mismatched_fields": list(evaluation.mismatched_fields),
                "derived_environment_reward": derived_reward,
            },
        },
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "passed_count": passed_count,
            "failed_count": len(checks) - passed_count,
            "passed": passed_count == len(checks),
        },
    }


def _guest_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture real guest browser-boundary evidence")
    parser.add_argument(
        "--guest-image",
        type=Path,
        default=Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2"),
    )
    parser.add_argument("--local-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--stop-loss-seconds", type=int, default=90 * 60)
    args = parser.parse_args(argv)
    if args.stop_loss_seconds <= 0:
        parser.error("--stop-loss-seconds must be positive")

    def stop_loss(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"guest browser probe exceeded {args.stop_loss_seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, stop_loss)
    signal.alarm(args.stop_loss_seconds)
    try:
        evidence = validate_guest_browser_boundary(
            Path(__file__).resolve().parents[2],
            guest_image_path=args.guest_image,
            screenshot_path=args.screenshot,
            seed=args.seed,
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    local_evidence = json.loads(args.local_evidence.read_text(encoding="utf-8"))
    if not isinstance(local_evidence, dict):
        raise TypeError("local browser-boundary evidence must be a JSON object")
    local_evidence["guest_navigation_surface"] = evidence
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(local_evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    passed = (
        evidence["summary"]["passed"]
        and browser_boundary_evidence_passed(local_evidence)
        and browser_boundary_source_hashes_match(local_evidence, Path(__file__).resolve().parents[2])
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(_guest_cli())
