"""Opt-in browser checks for the control-plane web UI (#61 scope item 8, #165).

Serves the real control app on loopback from an isolated, seeded control store and drives it
with Playwright Chromium. No Docker, MLflow, Metaflow, provider, or external network is used.
Run explicitly with ``PIXELGYM_RUN_BROWSER_TESTS=1`` when Playwright Chromium is installed:

    PIXELGYM_RUN_BROWSER_TESTS=1 pytest tests/integration/platform/test_web_browser_accessibility.py
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from pixelgym.platform.contracts import RunSummary
from pixelgym.platform.control_store import ControlStore, TransitionError
from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template
from pixelgym.platform.schema_validation import load_gate_policy
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance
from pixelgym.platform.web import create_control_app

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

pytestmark = [
    pytest.mark.browser_integration,
    pytest.mark.local_http_integration,
    pytest.mark.skipif(
        os.environ.get("PIXELGYM_RUN_BROWSER_TESTS") != "1",
        reason="set PIXELGYM_RUN_BROWSER_TESTS=1 to run browser integration tests",
    ),
]

REPOSITORY_ROOT = Path(__file__).parents[3]
CSRF_SECRET = "browser-test-csrf-secret-local-only"
MLFLOW_BASE_URL = "http://mlflow.invalid"
# WCAG 2.2 SC 1.4.11: a focus indicator needs at least 3:1 contrast with adjacent colours.
MINIMUM_FOCUS_CONTRAST = 3.0
LIVE_STATUS_TIMEOUT_MS = 10_000
SUBMIT_REQUEST = {
    "dataset": "day3-frozen-v1",
    "prompt_version": "1",
    "model": "day3-replay-baseline-v1",
    "condition": "raw",
    "maximum_calls": "100",
    "price_catalog": "pixelgym-demo-prices-v1",
}

FOCUS_PROBE = """(element) => {
  const parse = (value) => {
    const match = value.match(/rgba?\\(([^)]+)\\)/);
    if (!match) { return null; }
    const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
  };
  const luminance = ({r, g, b}) => {
    const channel = (value) => {
      const scaled = value / 255;
      return scaled <= 0.03928 ? scaled / 12.92 : Math.pow((scaled + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };
  let background = null;
  for (let node = element.parentElement; node && !background; node = node.parentElement) {
    const colour = parse(getComputedStyle(node).backgroundColor);
    if (colour && colour.a > 0) { background = colour; }
  }
  background = background || {r: 255, g: 255, b: 255, a: 1};
  const style = getComputedStyle(element);
  const ring = parse(style.outlineColor);
  const lighter = Math.max(luminance(ring), luminance(background));
  const darker = Math.min(luminance(ring), luminance(background));
  return {
    focused: document.activeElement === element,
    outlineStyle: style.outlineStyle,
    outlineWidth: parseFloat(style.outlineWidth),
    contrast: (lighter + 0.05) / (darker + 0.05),
  };
}"""

ACTIVE_ELEMENT = """() => {
  const element = document.activeElement;
  const box = element.getBoundingClientRect();
  return {
    tag: element.tagName.toLowerCase(),
    name: element.getAttribute("name") || "",
    text: (element.innerText || "").trim(),
    x: box.x + window.scrollX,
    y: box.y + window.scrollY,
  };
}"""

UNLABELLED_CONTROLS = """() => Array.from(
  document.querySelectorAll('input:not([type="hidden"]), select, textarea')
).filter((control) => !(
  (control.labels && control.labels.length > 0 &&
   Array.from(control.labels).some((label) => label.innerText.trim().length > 0)) ||
  (control.getAttribute("aria-label") || "").trim() ||
  control.getAttribute("aria-labelledby")
)).map((control) => control.outerHTML)"""


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _serve(app: Any) -> Iterator[str]:
    """Serve the control app on loopback and always shut it down."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, name="control-web-browser-server", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base_url}/static/platform.css", timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("control web browser server did not become ready")
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("control web browser server did not shut down")


class _RefusingCoordinator:
    """Makes deploy/rollback controls render; any action is refused without touching state."""

    def deploy(self, candidate_id: str, **_: Any) -> Any:
        raise TransitionError("browser fixture refuses deployment")

    def rollback(self, **_: Any) -> Any:
        raise TransitionError("browser fixture refuses rollback")


@dataclass
class Platform:
    url: str
    control: ControlStore
    candidates: dict[str, str]
    cancelled: list[str] = field(default_factory=list)

    def events(self, event_type: str) -> list[dict[str, Any]]:
        return [item for item in self.control.audit_events() if item["event_type"] == event_type]


def _policy(gate_policy: Any, version: int, model: str) -> Any:
    return build_policy_manifest(
        provider="scripted-demo",
        model=model,
        prompt_name=PROMPT_NAME,
        prompt_version=version,
        prompt=prompt_template(version),
        condition="raw",
        parameters={"deterministic": True, "hidden_retries": 0},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate_policy.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate_policy.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION, "a" * 40, "b" * 64, "clean", "git-build-inputs-v1"
        ),
        dependency_lock_sha256=dependency_lock_sha256(REPOSITORY_ROOT),
    )


def _register(
    control: ControlStore, gate_policy: Any, version: int, model: str, **summary_changes: Any
) -> Any:
    policy = _policy(gate_policy, version, model)
    summary = RunSummary(
        run_id=f"run-{model}",
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        policy_id=policy.policy_id,
        scorer_version=gate_policy.required_scorer_version,
        target_semantics=gate_policy.required_target_semantics,
        expected_count=100,
        scored_count=100,
        unique_record_count=100,
        correct_count=80,
        accuracy=0.8,
        cost_usd_per_100=0.0,
        priced_call_count=100,
        unpriced_call_count=0,
        provider_latency_p95_ms=100.0,
        latency_measured_count=100,
        code_state="clean",
        code_provenance_verified=True,
    )
    summary = replace(summary, **summary_changes)
    return control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=evaluate_gates(gate_policy, summary),
        artifacts=[],
        summary=summary,
    )


def _seed(control: ControlStore) -> dict[str, str]:
    gate_policy = load_gate_policy(REPOSITORY_ROOT)
    eligible = _register(control, gate_policy, 2, "browser-eligible")
    compatible = _register(control, gate_policy, 1, "browser-compatible")
    incompatible = _register(
        control, gate_policy, 2, "browser-other-metric", primary_metric="mean_distance"
    )
    previous = None
    deployed = []
    for model in ("browser-deployed-first", "browser-deployed-second"):
        candidate = _register(control, gate_policy, 2, model)
        control.approve(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="browser fixture approval",
            gate_report_sha256=candidate.gate_report_sha256,
        )
        previous = control.activate(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="browser fixture deployment",
            action="deploy",
            expected_deployment_id=None if previous is None else previous.deployment_id,
            expected_generation=0 if previous is None else previous.generation,
        )
        deployed.append(candidate.candidate_id)
    return {
        "eligible": eligible.candidate_id,
        "compatible": compatible.candidate_id,
        "incompatible": incompatible.candidate_id,
        "deployed": deployed[-1],
    }


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with playwright_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except playwright_api.Error as default_error:
            # Fall back to the full Chromium build when only it (not the headless shell)
            # is installed for this Playwright release.
            try:
                launched = playwright.chromium.launch(headless=True, channel="chromium")
            except playwright_api.Error:
                pytest.skip(f"Playwright Chromium is not installed: {default_error}")
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def platform(tmp_path: Path) -> Iterator[Platform]:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidates = _seed(control)
    cancelled: list[str] = []
    app = create_control_app(
        control,
        coordinator=_RefusingCoordinator(),  # type: ignore[arg-type]
        csrf_secret=CSRF_SECRET,
        submit_callback=lambda submission_id, payload: None,
        cancel_callback=lambda submission_id: cancelled.append(submission_id) or True,
        mlflow_base_url=MLFLOW_BASE_URL,
    )
    with _serve(app) as url:
        yield Platform(url=url, control=control, candidates=candidates, cancelled=cancelled)


@pytest.fixture
def page(browser: Any) -> Iterator[Any]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        opened = context.new_page()
        opened.set_default_timeout(10_000)
        yield opened
    finally:
        context.close()


def _assert_visible_focus(locator: Any, label: str) -> None:
    probe = locator.evaluate(FOCUS_PROBE)
    assert probe["focused"], label
    assert probe["outlineStyle"] not in {"none", "hidden"}, (label, probe)
    assert probe["outlineWidth"] >= 2, (label, probe)
    assert probe["contrast"] >= MINIMUM_FOCUS_CONTRAST, (label, probe)


def _tab_sequence(page: Any, count: int) -> list[dict[str, Any]]:
    sequence = []
    for _ in range(count):
        page.keyboard.press("Tab")
        sequence.append(page.evaluate(ACTIVE_ELEMENT))
        _assert_visible_focus(page.locator(":focus"), str(sequence[-1]))
    return sequence


def test_submit_form_tab_order_follows_reading_order_with_visible_focus(
    platform: Platform, page: Any
) -> None:
    page.goto(platform.url)
    sequence = _tab_sequence(page, 13)
    assert [(item["tag"], item["name"] or item["text"]) for item in sequence] == [
        ("a", "PIXELGYM CONTROL"),
        ("a", "Submit"),
        ("a", "Runs"),
        ("a", "Submissions"),
        ("a", "Compare"),
        ("a", "Deployment"),
        ("select", "dataset"),
        ("select", "prompt_version"),
        ("select", "model"),
        ("input", "condition"),
        ("input", "maximum_calls"),
        ("input", "price_catalog"),
        ("button", "Submit fixed evaluation →"),
    ]
    form_fields = [(round(item["y"]), round(item["x"])) for item in sequence[6:]]
    # The three-column grid is read row by row: never upward, and left to right within a row.
    assert form_fields == sorted(form_fields), form_fields


def test_action_forms_are_reachable_by_keyboard_in_order(platform: Platform, page: Any) -> None:
    candidate_id = platform.candidates["eligible"]
    page.goto(f"{platform.url}/candidates/{candidate_id}")
    reason = page.get_by_label("Approval reason")
    reason.focus()
    _assert_visible_focus(reason, "approval reason")
    page.keyboard.press("Tab")
    button = page.get_by_role("button", name="Approve exact candidate")
    _assert_visible_focus(button, "approve button")

    page.goto(f"{platform.url}/deployment")
    rollback_reason = page.get_by_label("Rollback reason")
    rollback_reason.focus()
    page.keyboard.press("Tab")
    rollback = page.get_by_role("button", name="Rollback to previous approved version")
    _assert_visible_focus(rollback, "rollback button")
    note = page.locator("#rollback-validation")
    playwright_api.expect(note).to_be_visible()
    playwright_api.expect(note).to_contain_text("Load and health validation happens on action")
    playwright_api.expect(rollback).to_have_attribute("aria-describedby", "rollback-validation")


def test_every_form_control_has_a_visible_label(platform: Platform, page: Any) -> None:
    submission_id = platform.control.submit(SUBMIT_REQUEST)
    pages = [
        "/",
        "/runs",
        "/submissions",
        "/compare",
        f"/candidates/{platform.candidates['eligible']}",
        f"/submissions/{submission_id}",
        "/deployment",
    ]
    for path in pages:
        page.goto(f"{platform.url}{path}")
        assert page.locator("input, select, textarea").count() > 0, path
        assert page.evaluate(UNLABELLED_CONTROLS) == [], path
    page.goto(platform.url)
    for label, name in (
        ("Dataset snapshot", "dataset"),
        ("Prompt version", "prompt_version"),
        ("Provider model", "model"),
        ("Condition", "condition"),
        ("Maximum calls", "maximum_calls"),
        ("Price catalog", "price_catalog"),
    ):
        # An implicit label's accessible name also carries the embedded control's value.
        playwright_api.expect(page.get_by_label(label)).to_have_attribute("name", name)


def test_blocked_duplicate_approval_moves_focus_to_the_error_summary(
    platform: Platform, page: Any
) -> None:
    candidate_url = f"{platform.url}/candidates/{platform.candidates['eligible']}"
    stale = page.context.new_page()
    stale.goto(candidate_url)
    page.goto(candidate_url)
    page.get_by_label("Approval reason").fill("first reviewer approval")
    page.get_by_role("button", name="Approve exact candidate").click()
    playwright_api.expect(page.locator(".action-panel h2")).to_have_text("Approved")

    # The stale tab still shows the approval form; a second, different approval is refused.
    stale.get_by_label("Approval reason").fill("duplicate approval from a stale tab")
    stale.get_by_role("button", name="Approve exact candidate").click()
    summary = stale.locator(".error-summary")
    playwright_api.expect(summary).to_be_visible()
    playwright_api.expect(summary).to_have_attribute("role", "alert")
    playwright_api.expect(summary).to_be_focused()
    _assert_visible_focus(summary, "error summary")
    playwright_api.expect(summary).to_contain_text("already approved with different evidence")
    approvals = [
        item
        for item in platform.events("candidate.approved")
        if item["subject_id"] == platform.candidates["eligible"]
    ]
    assert len(approvals) == 1


def test_repeated_click_submission_is_sent_once_and_resubmission_is_idempotent(
    platform: Platform, page: Any
) -> None:
    # Hold the first POST in flight so a second click lands while the page is still open,
    # as it does for an impatient reviewer on a slow evaluation start.
    held: list[Any] = []
    page.route(f"{platform.url}/experiments", lambda route: held.append(route))
    page.goto(platform.url)
    button = page.get_by_role("button", name="Submit fixed evaluation")
    box = button.bounding_box()
    assert box is not None
    centre = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.click(*centre)
    page.wait_for_timeout(300)
    page.mouse.click(*centre)
    page.wait_for_timeout(300)
    assert len(held) == 1
    held[0].continue_()
    page.wait_for_url(f"{platform.url}/submissions/submission-*")
    page.unroute(f"{platform.url}/experiments")
    first_url = page.url
    assert len(platform.events("submission.created")) == 1
    assert platform.events("submission.resubmitted") == []

    page.goto(platform.url)
    page.get_by_role("button", name="Submit fixed evaluation").click()
    page.wait_for_url(f"{platform.url}/submissions/submission-*")
    assert page.url == first_url
    assert len(platform.events("submission.created")) == 1
    assert len(platform.events("submission.resubmitted")) == 1

    submission_id = first_url.rsplit("/", 1)[1]
    page.goto(f"{platform.url}/submissions")
    playwright_api.expect(page.locator(f'a[href="/submissions/{submission_id}"]')).to_have_count(1)


def test_submission_page_shows_live_status_transitions_without_reload(
    platform: Platform, page: Any
) -> None:
    submission_id = platform.control.submit(SUBMIT_REQUEST)
    page.goto(f"{platform.url}/submissions/{submission_id}")
    status = page.locator('[data-live="status"]')
    playwright_api.expect(status).to_have_text("Submitted")
    playwright_api.expect(page.locator('[data-live="pathspec"]')).to_have_text("pending")
    page.evaluate("window.__pixelgymNoReload = true")

    platform.control.link_run(
        submission_id, metaflow_pathspec="GroundingEvaluationFlow/41", mlflow_run_id="run-live"
    )
    playwright_api.expect(status).to_have_text("Running", timeout=LIVE_STATUS_TIMEOUT_MS)
    playwright_api.expect(page.locator('[data-live="status-badge"]')).to_have_text("Running")
    playwright_api.expect(page.locator('[data-live="pathspec"]')).to_have_text(
        "GroundingEvaluationFlow/41"
    )
    playwright_api.expect(page.locator('[data-live="mlflow"] a')).to_have_attribute(
        "href", f"{MLFLOW_BASE_URL}/#/experiments/0/runs/run-live"
    )
    playwright_api.expect(page.get_by_role("button", name="Cancel experiment")).to_be_visible()

    platform.control.mark_submission(submission_id, "Failed")
    playwright_api.expect(status).to_have_text("Failed", timeout=LIVE_STATUS_TIMEOUT_MS)
    playwright_api.expect(page.get_by_role("button", name="Cancel experiment")).to_be_hidden()
    playwright_api.expect(page.locator('[data-live="cancel-closed"]')).to_be_visible()
    playwright_api.expect(page.locator('[data-live="partial-evidence"]')).to_be_visible()
    assert page.evaluate("window.__pixelgymNoReload === true")
    assert status.get_attribute("role") == "status"


def test_running_submission_can_be_cancelled_by_keyboard(platform: Platform, page: Any) -> None:
    submission_id = platform.control.submit(SUBMIT_REQUEST)
    platform.control.link_run(
        submission_id, metaflow_pathspec="GroundingEvaluationFlow/42", mlflow_run_id="run-cancel"
    )
    page.goto(f"{platform.url}/submissions/{submission_id}")
    reason = page.get_by_label("Cancellation reason")
    reason.focus()
    page.keyboard.type("stop the browser fixture run")
    page.keyboard.press("Tab")
    _assert_visible_focus(page.get_by_role("button", name="Cancel experiment"), "cancel button")
    page.keyboard.press("Enter")
    playwright_api.expect(page.locator('[data-live="status"]')).to_have_text("Cancelled")
    playwright_api.expect(page.get_by_role("button", name="Cancel experiment")).to_have_count(0)
    playwright_api.expect(page.get_by_text("Cancellation is no longer available")).to_be_visible()
    playwright_api.expect(page.locator('[data-live="partial-evidence"]')).to_be_visible()
    assert platform.cancelled == [submission_id]
    assert platform.control.get_submission(submission_id)["status"] == "Cancelled"
    assert len(platform.events("submission.cancelled")) == 1

    page.goto(f"{platform.url}/submissions?status=Cancelled")
    row = page.locator("tbody tr", has_text=submission_id)
    playwright_api.expect(row).to_contain_text("Cancelled")
    playwright_api.expect(row).to_contain_text("GroundingEvaluationFlow/42")
    playwright_api.expect(row).to_contain_text("No candidate registered")
    playwright_api.expect(row).to_contain_text("Partial evidence retained")


def test_incompatible_comparison_is_blocked_and_compatible_one_is_not(
    platform: Platform, page: Any
) -> None:
    def compare(*candidate_ids: str) -> None:
        page.goto(f"{platform.url}/compare")
        for candidate_id in candidate_ids:
            checkbox = page.locator(f'input[name="candidate"][value="{candidate_id}"]')
            checkbox.focus()
            _assert_visible_focus(checkbox, candidate_id)
            page.keyboard.press("Space")
            playwright_api.expect(checkbox).to_be_checked()
        page.get_by_role("button", name="Compare selected").focus()
        page.keyboard.press("Enter")
        page.wait_for_url(f"{platform.url}/compare?*")

    compare(platform.candidates["eligible"], platform.candidates["incompatible"])
    playwright_api.expect(page.get_by_text("PROMOTION COMPARISON BLOCKED")).to_be_visible()
    playwright_api.expect(page.get_by_text("recorded primary metric")).to_be_visible()
    playwright_api.expect(page.locator(".badge", has_text="COMPATIBLE")).to_have_count(0)

    compare(platform.candidates["eligible"], platform.candidates["compatible"])
    playwright_api.expect(page.locator(".badge", has_text="COMPATIBLE")).to_have_count(1)
    playwright_api.expect(page.get_by_text("PROMOTION COMPARISON BLOCKED")).to_have_count(0)
