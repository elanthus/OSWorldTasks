"""Deterministic build-time browser capture for the grounding dataset."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.grounding.schema import (
    CANDIDATE_SCHEMA_VERSION,
    CAPTURE_VERSION,
    CSS_HEIGHT,
    CSS_WIDTH,
    DEVICE_SCALE_FACTOR,
    EXAMPLE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    SCREEN_STATES,
    TARGET_SPECS,
    TASK_SEEDS,
    css_bbox_to_screenshot,
    target_for_example_index,
    validate_candidate_set,
    validate_example,
)
from pixelgym.serialization import canonical_json_text, load_jsonl
from pixelgym.tasks.vendor_form.browser_contract import (
    BROWSER_ARGS,
    READY_SELECTOR,
    local_vendor_form_server,
)
from pixelgym.tasks.vendor_form.ui import INCOMPLETE_SUBMISSION_MESSAGE

CAPTURE_SUMMARY_SCHEMA_VERSION = "pixelgym-grounding-capture-summary-v3"
# app.js cannot import this shared Python constant, so it carries the same text with a matching
# synchronization comment. Capture asserts the settled browser message before retaining a record.
_VALIDATION_MESSAGE = INCOMPLETE_SUBMISSION_MESSAGE
_CAPTURE_SOURCE_PATHS = (
    "pixelgym/grounding/capture.py",
    "pixelgym/grounding/schema.py",
    "pixelgym/tasks/vendor_form/app/server.py",
    "pixelgym/tasks/vendor_form/browser_contract.py",
    "pixelgym/tasks/vendor_form/ui.py",
)
_CAPTURE_STATIC_ROOT = Path("pixelgym/tasks/vendor_form/app/static")

# Backward-compatible public alias for callers that used the original capture helper.
local_capture_server = local_vendor_form_server

_CANDIDATE_SCRIPT = """
() => {
  const result = [];
  const add = (semanticId, elementType, visibleLabel, element) => {
    const rect = element.getBoundingClientRect();
    result.push({
      semantic_id: semanticId,
      element_type: elementType,
      visible_label: visibleLabel,
      css_bbox: [rect.x, rect.y, rect.right, rect.bottom],
    });
  };
  add("company_name", "text_input", "Company name", document.querySelector("#company_name"));
  add("contact_email", "text_input", "Contact email", document.querySelector("#contact_email"));
  add("contact_phone", "text_input", "Contact phone", document.querySelector("#contact_phone"));
  add("tax_id", "text_input", "Tax ID", document.querySelector("#tax_id"));
  add("country", "select", "Country", document.querySelector("#country"));
  document.querySelectorAll('#payment_terms input[type="radio"]').forEach((input) => {
    const label = input.closest("label");
    const slug = input.value.toLowerCase().replaceAll(" ", "_");
    add(`payment_terms_${slug}`, "radio", input.value, label);
  });
  const checkbox = document.querySelector("#expedited_onboarding");
  add("expedited_onboarding", "checkbox", "Expedited onboarding", checkbox.closest("label"));
  add("submit", "button", "Submit", document.querySelector("#submit-button"));
  return {
    css_width: window.innerWidth,
    css_height: window.innerHeight,
    device_scale_factor: window.devicePixelRatio,
    candidates: result,
    body_text: document.body.innerText,
  };
}
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_source_hashes(repository_root: Path) -> dict[str, str]:
    """Hash every application and capture source used to render the frozen images."""
    relative_paths = list(_CAPTURE_SOURCE_PATHS)
    relative_paths.extend(
        path.relative_to(repository_root).as_posix()
        for path in sorted((repository_root / _CAPTURE_STATIC_ROOT).rglob("*"))
        if path.is_file()
    )
    return {
        relative: _sha256((repository_root / relative).read_bytes()) for relative in relative_paths
    }


def _post_reset(base_url: str, seed: int) -> dict[str, Any]:
    body = canonical_json_text({"seed": seed}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/reset",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.load(response)


def _apply_state(page: Any, state: str, task: dict[str, Any]) -> None:
    fields = task["fields"]
    if state == "initial":
        return
    if state == "text_field_focused":
        page.locator("#company_name").focus()
        return
    if state == "validation_error":
        # Deliberately drive the real public submission path in this isolated build-time server.
        # Each example is reset immediately before this call, so submission number 1 proves the
        # incomplete attempt reached privileged state without contaminating another example.
        with page.expect_response(
            lambda response: response.url.endswith("/api/submit")
        ) as submission_response:
            page.locator("#submit-button").click()
        response = submission_response.value
        if not response.ok or response.json() != {"submission_number": 1}:
            raise RuntimeError("validation-error submission attempt was not recorded")
        if page.locator("#submit-status").inner_text() != _VALIDATION_MESSAGE:
            raise RuntimeError("deterministic validation message was not displayed")
        return
    if state in {"partially_completed", "completed_review"}:
        page.locator("#company_name").fill(fields["company_name"])
        page.locator("#contact_email").fill(fields["contact_email"])
        page.locator("#country").select_option(fields["country"])
    if state == "partially_completed":
        page.locator("#country").focus()
        return
    if state == "completed_review":
        page.locator("#contact_phone").fill(fields["contact_phone"])
        page.locator("#tax_id").fill(fields["tax_id"])
        page.locator(f'#payment_terms input[value="{fields["payment_terms"]}"]').set_checked(True)
        page.locator("#expedited_onboarding").set_checked(fields["expedited_onboarding"])
        page.locator("#submit-button").focus()
        return
    raise ValueError(f"unknown screen state {state!r}")


def _candidate_record(
    raw: dict[str, Any], *, screen_width: int, screen_height: int
) -> dict[str, Any]:
    css_bbox = [float(value) for value in raw["css_bbox"]]
    return {
        "semantic_id": raw["semantic_id"],
        "element_type": raw["element_type"],
        "visible_label": raw["visible_label"],
        "css_bbox": css_bbox,
        "bbox": css_bbox_to_screenshot(
            css_bbox,
            css_width=CSS_WIDTH,
            css_height=CSS_HEIGHT,
            screen_width=screen_width,
            screen_height=screen_height,
        ),
    }


def build_contact_sheet(
    examples: list[dict[str, Any]], *, repository_root: Path, output_path: Path
) -> None:
    """Render all target annotations into one deterministic visual-audit sheet."""
    columns = 5
    thumb_width, thumb_height, caption_height = 256, 192, 48
    rows = (len(examples) + columns - 1) // columns
    sheet = Image.new(
        "RGB", (columns * thumb_width, rows * (thumb_height + caption_height)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, example in enumerate(examples):
        image = Image.open(repository_root / example["image_path"]).convert("RGB")
        image.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
        column, row = index % columns, index // columns
        origin_x = column * thumb_width
        origin_y = row * (thumb_height + caption_height)
        sheet.paste(image, (origin_x, origin_y))
        scale_x = image.width / example["screen_width"]
        scale_y = image.height / example["screen_height"]
        x0, y0, x1, y1 = example["bbox"]
        draw.rectangle(
            (
                origin_x + round(x0 * scale_x),
                origin_y + round(y0 * scale_y),
                origin_x + round(x1 * scale_x) - 1,
                origin_y + round(y1 * scale_y) - 1,
            ),
            outline=(220, 20, 60),
            width=2,
        )
        caption = f"{example['example_id']}\n{example['target']}"
        draw.multiline_text(
            (origin_x + 4, origin_y + thumb_height + 3),
            caption,
            font=font,
            fill=(20, 20, 20),
            spacing=2,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=9)


def validate_dataset(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    repository_root: Path,
) -> dict[str, Any]:
    if len(examples) != len(TASK_SEEDS) * len(SCREEN_STATES):
        raise ValueError("dataset does not contain the frozen 100-example grid")
    if len(candidate_records) != len(examples):
        raise ValueError("candidate metadata is not one-to-one with examples")
    example_ids: set[str] = set()
    triples: set[tuple[str, str, tuple[int, ...]]] = set()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    target_state_counts: Counter[tuple[str, str]] = Counter()
    candidates_by_example: dict[str, dict[str, Any]] = {}
    for record in candidate_records:
        if set(record) != {"schema_version", "protocol_version", "example_id", "candidates"}:
            raise ValueError("candidate record fields do not match the frozen schema")
        if record["schema_version"] != CANDIDATE_SCHEMA_VERSION:
            raise ValueError("candidate schema version does not match")
        if record["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("candidate protocol version does not match")
        if record["example_id"] in candidates_by_example:
            raise ValueError("duplicate candidate example_id")
        candidates_by_example[record["example_id"]] = record
    for example in examples:
        validate_example(example)
        if example["example_id"] in example_ids:
            raise ValueError("duplicate example_id")
        example_ids.add(example["example_id"])
        image_bytes = (repository_root / example["image_path"]).read_bytes()
        if _sha256(image_bytes) != example["image_sha256"]:
            raise ValueError("image digest does not match dataset record")
        with Image.open(repository_root / example["image_path"]) as image:
            if image.size != (example["screen_width"], example["screen_height"]):
                raise ValueError("decoded image dimensions do not match dataset record")
        triple = (example["image_sha256"], example["target"], tuple(example["bbox"]))
        if triple in triples:
            raise ValueError("duplicate (image, target, bbox) record")
        triples.add(triple)
        target_counts[example["target_id"]] += 1
        state_counts[example["screen_state"]] += 1
        target_state_counts[(example["target_id"], example["screen_state"])] += 1
        candidates = candidates_by_example[example["example_id"]]["candidates"]
        validate_candidate_set(
            candidates, width=example["screen_width"], height=example["screen_height"]
        )
        matches = [c for c in candidates if c["semantic_id"] == example["target_id"]]
        if len(matches) != 1 or matches[0]["bbox"] != example["bbox"]:
            raise ValueError("target does not match exactly one independently collected candidate")
    if set(target_counts.values()) != {10} or set(target_counts) != {
        spec.semantic_id for spec in TARGET_SPECS
    }:
        raise ValueError("target allocation is not balanced at ten examples each")
    if set(state_counts.values()) != {20} or set(state_counts) != set(SCREEN_STATES):
        raise ValueError("screen-state allocation is not balanced at twenty examples each")
    target_screen_states = {
        target_id: sorted(
            state for state in SCREEN_STATES if target_state_counts[(target_id, state)]
        )
        for target_id in sorted(target_counts)
    }
    perfect_aliasing = all(len(states) == 1 for states in target_screen_states.values())
    return {
        "summary_schema_version": CAPTURE_SUMMARY_SCHEMA_VERSION,
        "example_count": len(examples),
        "candidate_record_count": len(candidate_records),
        "target_counts": dict(sorted(target_counts.items())),
        "screen_state_counts": dict(sorted(state_counts.items())),
        "target_screen_states": target_screen_states,
        "target_screen_state_perfect_aliasing": perfect_aliasing,
        "automatic_integrity_checks_passed": True,
        "known_design_limitations": (
            [
                {
                    "code": "target_screen_state_perfect_aliasing",
                    "effect": (
                        "target identity and screen state effects are not independently "
                        "identifiable"
                    ),
                }
            ]
            if perfect_aliasing
            else []
        ),
    }


def refresh_capture_summary(repository_root: Path) -> dict[str, Any]:
    """Revalidate frozen capture evidence without launching a browser or rewriting assets."""
    artifact_root = repository_root / "artifacts"
    capture_path = artifact_root / "grounding-capture.json"
    prior = json.loads(capture_path.read_text(encoding="utf-8"))
    examples = load_jsonl(artifact_root / "grounding-dataset.jsonl")
    candidate_records = load_jsonl(artifact_root / "grounding-candidates.jsonl")
    summary = validate_dataset(examples, candidate_records, repository_root=repository_root)
    summary.update(
        {
            "protocol_version": PROTOCOL_VERSION,
            "capture_version": CAPTURE_VERSION,
            "browser_engine": prior["browser_engine"],
            "browser_version": prior["browser_version"],
            "browser_args": prior["browser_args"],
            "dataset_path": "artifacts/grounding-dataset.jsonl",
            "candidates_path": "artifacts/grounding-candidates.jsonl",
            "contact_sheet_path": "artifacts/grounding/contact-sheet.png",
            "source_sha256": prior["source_sha256"],
            "summary_provenance": {
                "mode": "offline_revalidation_of_frozen_capture",
                "browser_recapture_performed": False,
            },
        }
    )
    capture_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def capture_dataset(repository_root: Path) -> dict[str, Any]:
    """Capture the frozen dataset and return its structured validation summary."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without dev dependencies
        raise RuntimeError('capture requires `pip install -e ".[dev]"`') from exc

    artifact_root = repository_root / "artifacts"
    image_dir = artifact_root / "grounding" / "images" / "raw"
    image_dir.mkdir(parents=True, exist_ok=True)
    examples: list[dict[str, Any]] = []
    candidate_records: list[dict[str, Any]] = []
    browser_version = "unknown"
    with local_vendor_form_server() as base_url, sync_playwright() as playwright:
        chromium_executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(BROWSER_ARGS)}
        if chromium_executable.is_file():
            launch_options["executable_path"] = str(chromium_executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            raise RuntimeError(
                "capture requires the Playwright Chromium binary; run "
                "`python -m playwright install chromium` after installing the dev extra"
            ) from exc
        browser_version = browser.version
        context = browser.new_context(
            viewport={"width": CSS_WIDTH, "height": CSS_HEIGHT},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
        )
        page = context.new_page()
        example_index = 0
        try:
            for seed in TASK_SEEDS:
                for state in SCREEN_STATES:
                    reset = _post_reset(base_url, seed)
                    page.goto(base_url, wait_until="networkidle")
                    page.locator(READY_SELECTOR).wait_for(state="attached")
                    page.evaluate("() => document.fonts.ready")
                    task = page.evaluate(
                        "() => fetch('/api/task').then(response => response.json())"
                    )
                    _apply_state(page, state, task)
                    if state == "initial":
                        page.evaluate(
                            "() => document.activeElement && document.activeElement.blur()"
                        )
                    instrumentation = page.evaluate(_CANDIDATE_SCRIPT)
                    if (
                        instrumentation["css_width"] != CSS_WIDTH
                        or instrumentation["css_height"] != CSS_HEIGHT
                        or instrumentation["device_scale_factor"] != DEVICE_SCALE_FACTOR
                    ):
                        raise RuntimeError("browser viewport does not match the frozen protocol")
                    privileged_terms = ("submitted_at_step", '"submissions"', "/api/state")
                    if any(term in instrumentation["body_text"] for term in privileged_terms):
                        raise RuntimeError(
                            "privileged evaluator data appeared in the screenshot DOM"
                        )

                    number = example_index + 1
                    image_path = image_dir / f"vendor-form-{number:04d}.png"
                    image_bytes = page.screenshot(type="png", animations="disabled")
                    image_path.write_bytes(image_bytes)
                    with Image.open(image_path) as decoded:
                        screen_width, screen_height = decoded.size
                    candidates = [
                        _candidate_record(
                            candidate,
                            screen_width=screen_width,
                            screen_height=screen_height,
                        )
                        for candidate in instrumentation["candidates"]
                    ]
                    validate_candidate_set(candidates, width=screen_width, height=screen_height)

                    # The target is consulted only after the full candidate set is complete.
                    target = target_for_example_index(example_index)
                    target_candidate = next(
                        candidate
                        for candidate in candidates
                        if candidate["semantic_id"] == target.semantic_id
                    )
                    slug = target.semantic_id.replace("_", "-")
                    example_id = f"vendor-form-{number:04d}-{slug}"
                    relative_image_path = image_path.relative_to(repository_root).as_posix()
                    example = {
                        "schema_version": EXAMPLE_SCHEMA_VERSION,
                        "protocol_version": PROTOCOL_VERSION,
                        "example_id": example_id,
                        "image_path": relative_image_path,
                        "image_sha256": _sha256(image_bytes),
                        "target_id": target.semantic_id,
                        "target": target.instruction,
                        "bbox": target_candidate["bbox"],
                        "css_bbox": target_candidate["css_bbox"],
                        "element_type": target.element_type,
                        "task_seed": seed,
                        "task_id": reset["task_id"],
                        "screen_state": state,
                        "css_width": CSS_WIDTH,
                        "css_height": CSS_HEIGHT,
                        "screen_width": screen_width,
                        "screen_height": screen_height,
                        "device_scale_factor": DEVICE_SCALE_FACTOR,
                        "capture_version": CAPTURE_VERSION,
                    }
                    validate_example(example)
                    examples.append(example)
                    candidate_records.append(
                        {
                            "schema_version": CANDIDATE_SCHEMA_VERSION,
                            "protocol_version": PROTOCOL_VERSION,
                            "example_id": example_id,
                            "candidates": candidates,
                        }
                    )
                    example_index += 1
        finally:
            context.close()
            browser.close()

    summary = validate_dataset(examples, candidate_records, repository_root=repository_root)
    dataset_path = artifact_root / "grounding-dataset.jsonl"
    candidates_path = artifact_root / "grounding-candidates.jsonl"
    dataset_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in examples), encoding="utf-8"
    )
    candidates_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in candidate_records),
        encoding="utf-8",
    )
    contact_sheet_path = artifact_root / "grounding" / "contact-sheet.png"
    build_contact_sheet(examples, repository_root=repository_root, output_path=contact_sheet_path)
    summary.update(
        {
            "protocol_version": PROTOCOL_VERSION,
            "capture_version": CAPTURE_VERSION,
            "browser_engine": "chromium",
            "browser_version": browser_version,
            "browser_args": list(BROWSER_ARGS),
            "dataset_path": dataset_path.relative_to(repository_root).as_posix(),
            "candidates_path": candidates_path.relative_to(repository_root).as_posix(),
            "contact_sheet_path": contact_sheet_path.relative_to(repository_root).as_posix(),
            "source_sha256": capture_source_hashes(repository_root),
            "summary_provenance": {
                "mode": "browser_capture",
                "browser_recapture_performed": True,
            },
        }
    )
    (artifact_root / "grounding-capture.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary
