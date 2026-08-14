"""Browser-level evidence for the incomplete vendor-form submission boundary."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

from pixelgym.evaluator import evaluate
from pixelgym.grounding.capture import _BROWSER_ARGS, _READY_SELECTOR, local_capture_server
from pixelgym.grounding.schema import CSS_HEIGHT, CSS_WIDTH, DEVICE_SCALE_FACTOR
from pixelgym.task_spec import Submission, TaskSpec
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE

BROWSER_BOUNDARY_SCHEMA_VERSION = "pixelgym-browser-boundary-v1"
BROWSER_BOUNDARY_VALIDATOR = "vendor-form-browser-boundary"

SOURCE_PATHS = (
    "pixelgym/validation/browser_boundary.py",
    "pixelgym/tasks/vendor_form/app/static/app.js",
    "pixelgym/tasks/vendor_form/app/server.py",
    "pixelgym/evaluator.py",
    "pixelgym/env.py",
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(repository_root: Path) -> dict[str, str]:
    """Hash every implementation source that the browser-boundary result depends on."""
    return {relative: _sha256(repository_root / relative) for relative in SOURCE_PATHS}


def _json_request(url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.load(response)


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
    if not isinstance(browser, dict) or browser.get("engine") != "chromium":
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


def validate_browser_boundary(repository_root: Path, *, seed: int = 7) -> dict[str, Any]:
    """Exercise the real JavaScript POST path and return structured privileged evidence."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional browser tooling
        raise RuntimeError('browser validation requires `pip install -e ".[dev]"`') from exc

    with local_capture_server() as base_url, sync_playwright() as playwright:
        reset = _json_request(f"{base_url}/api/reset", payload={"seed": seed})
        chromium_executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(_BROWSER_ARGS)}
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
            page.locator(_READY_SELECTOR).wait_for(state="attached")
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
            "args": list(_BROWSER_ARGS),
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
