"""Deterministic capture pipeline for the v4 compositional grounding pilot."""

from __future__ import annotations

import hashlib
import json
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pixelgym.grounding.capture import _candidate_record, _post_reset
from pixelgym.grounding.determinism import compare_png_directories
from pixelgym.grounding.overlays import proposal_match, render_overlay
from pixelgym.grounding.schema import (
    CAPTURE_VERSION,
    CSS_HEIGHT,
    CSS_WIDTH,
    DEVICE_SCALE_FACTOR,
    SCREEN_STATES,
    TASK_SEEDS,
    TargetSpec,
    validate_bbox,
    validate_candidate_set,
)
from pixelgym.grounding.v4_server import V4_READY_SELECTOR, V4_SEEDS, local_v4_server
from pixelgym.serialization import canonical_json_text

V4_PROTOCOL_VERSION = "pixelgym-grounding-v4-pilot"
V4_EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-v4-example-v1"
V4_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-v4-candidates-v1"
V4_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-v4-overlay-v1"
V4_CAPTURE_SCHEMA_VERSION = "pixelgym-grounding-v4-capture-v1"
V4_MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-v4-manifest-v1"
V4_CALIBRATION_SEEDS = V4_SEEDS
V4_EXPECTED_CANDIDATE_COUNT = 39

V4_CANDIDATE_IDS = (
    "nav_queue",
    "nav_vendors",
    "nav_policies",
    "nav_audit",
    "sb_inbox",
    "sb_pending",
    "sb_exceptions",
    "sb_approved",
    "sb_archived",
    "route_standard",
    "route_finance",
    "route_compliance",
    "billing_email",
    "vat_number",
    "search_input",
    "refresh_queue",
    *(
        f"{control}_{vendor}"
        for vendor in ("acme", "meridian", "pacific", "atlas", "harbor")
        for control in ("name", "view", "edit", "deactivate")
    ),
    "review_existing_atlas",
    "keep_current",
    "create_new",
)

V4_TARGET_SPECS = (
    TargetSpec(
        "edit_meridian",
        "Click Edit for the pending vendor with the highest annual spend",
        "button",
    ),
    TargetSpec(
        "deactivate_harbor",
        "Click Deactivate for the only inactive vendor in the United States",
        "button",
    ),
    TargetSpec(
        "billing_email",
        "Click the field identified by the validation message",
        "text_input",
    ),
    TargetSpec(
        "route_compliance",
        "Apply the visible approval policy to the current request and click the required route",
        "button",
    ),
    TargetSpec(
        "review_existing_atlas",
        "Use the duplicate warning and request card to review the matching existing vendor",
        "button",
    ),
    TargetSpec(
        "edit_pacific",
        "Click Edit for the pending vendor with the earliest renewal date",
        "button",
    ),
    TargetSpec(
        "view_acme",
        "Click View for the verified active vendor with the lowest risk",
        "button",
    ),
    TargetSpec(
        "vat_number",
        "Click the field identified by the validation message",
        "text_input",
    ),
    TargetSpec(
        "route_finance",
        "Apply the visible approval policy to the current request and click the required route",
        "button",
    ),
    TargetSpec(
        "keep_current",
        "The resolution message says the requested value is already set; click the safe action",
        "button",
    ),
)

V4_TARGET_FAMILIES = {
    "edit_meridian": "relational_table",
    "deactivate_harbor": "relational_table",
    "edit_pacific": "relational_table",
    "view_acme": "relational_table",
    "route_compliance": "cross_panel_policy",
    "review_existing_atlas": "cross_panel_policy",
    "route_finance": "cross_panel_policy",
    "billing_email": "recovery_state",
    "vat_number": "recovery_state",
    "keep_current": "recovery_state",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_V4_CAPTURE_SOURCE_PATHS = (
    "pixelgym/grounding/calibration_v4.py",
    "pixelgym/grounding/v4_server.py",
    "pixelgym/grounding/v4_app/static/app.js",
    "pixelgym/grounding/v4_app/static/index.html",
    "pixelgym/grounding/v4_app/static/style.css",
    "pixelgym/grounding/v3c_app/static/fonts/DejaVuSans.ttf",
    "pixelgym/grounding/v3c_app/static/fonts/DejaVuSans-Bold.ttf",
)


def v4_capture_source_hashes(repository_root: Path) -> dict[str, str]:
    return {
        relative: _sha256((repository_root / relative).read_bytes())
        for relative in _V4_CAPTURE_SOURCE_PATHS
    }


def v4_calibration_target(seed: int, screen_state: str) -> TargetSpec:
    """Return the frozen target for one of the ten v4 seed/state cells."""
    if seed in TASK_SEEDS:
        raise ValueError("v4 calibration seed overlaps a frozen scored seed")
    if seed not in V4_CALIBRATION_SEEDS:
        raise ValueError("seed is outside the v4 calibration seed set")
    try:
        state_index = SCREEN_STATES.index(screen_state)
    except ValueError as exc:
        raise ValueError("state is outside the frozen screen-state set") from exc
    seed_index = V4_CALIBRATION_SEEDS.index(seed)
    return V4_TARGET_SPECS[seed_index * len(SCREEN_STATES) + state_index]


_V4_CANDIDATE_SCRIPT = """
() => {
  const result = [];
  const add = (semanticId, elementType, visibleLabel, selector) => {
    const element = document.querySelector(selector);
    if (!element) throw new Error("missing candidate " + semanticId);
    const rect = element.getBoundingClientRect();
    result.push({
      semantic_id: semanticId,
      element_type: elementType,
      visible_label: visibleLabel,
      css_bbox: [rect.x, rect.y, rect.right, rect.bottom],
    });
  };
  add("nav_queue", "link", "Review Queue", "#nav-queue");
  add("nav_vendors", "link", "Vendors", "#nav-vendors");
  add("nav_policies", "link", "Policies", "#nav-policies");
  add("nav_audit", "link", "Audit", "#nav-audit");
  add("sb_inbox", "link", "Inbox", "#sb-inbox");
  add("sb_pending", "link", "Pending", "#sb-pending");
  add("sb_exceptions", "link", "Exceptions", "#sb-exceptions");
  add("sb_approved", "link", "Approved", "#sb-approved");
  add("sb_archived", "link", "Archived", "#sb-archived");
  add("route_standard", "button", "Standard", "#route-standard");
  add("route_finance", "button", "Finance", "#route-finance");
  add("route_compliance", "button", "Compliance", "#route-compliance");
  add("billing_email", "text_input", "Billing email", "#billing-email");
  add("vat_number", "text_input", "VAT number", "#vat-number");
  add("search_input", "text_input", "Search queue...", "#search-input");
  add("refresh_queue", "button", "Refresh", "#refresh-queue");
  for (const vendor of ["acme", "meridian", "pacific", "atlas", "harbor"]) {
    const label = document.querySelector("#name-" + vendor).textContent;
    add("name_" + vendor, "link", label, "#name-" + vendor);
    add("view_" + vendor, "button", "View", "#view-" + vendor);
    add("edit_" + vendor, "button", "Edit", "#edit-" + vendor);
    add("deactivate_" + vendor, "button", "Deactivate", "#deactivate-" + vendor);
  }
  add(
    "review_existing_atlas",
    "button",
    "Review existing Atlas",
    "#review-existing-atlas"
  );
  add("keep_current", "button", "Keep current value", "#keep-current");
  add("create_new", "button", "Create new vendor", "#create-new");
  return {
    css_width: window.innerWidth,
    css_height: window.innerHeight,
    device_scale_factor: window.devicePixelRatio,
    candidates: result,
  };
}
"""


def _apply_v4_state(page: Any, *, state: str, seed: int) -> None:
    page.evaluate(
        "([state, seed]) => window.pixelgymV4.setCaptureState(state, seed)",
        [state, seed],
    )
    if state == "initial":
        page.evaluate("() => document.activeElement && document.activeElement.blur()")


def validate_v4_calibration_dataset(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the exact ten-example v4 allocation and leakage boundaries."""
    expected_count = len(V4_CALIBRATION_SEEDS) * len(SCREEN_STATES)
    if len(examples) != expected_count or len(candidate_records) != expected_count:
        raise ValueError(f"v4 calibration dataset must contain exactly {expected_count} examples")

    records_by_id: dict[str, dict[str, Any]] = {}
    for record in candidate_records:
        if record.get("schema_version") != V4_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("v4 candidate schema version does not match")
        if record.get("protocol_version") != V4_PROTOCOL_VERSION:
            raise ValueError("v4 candidate protocol version does not match")
        example_id = record.get("example_id")
        if not isinstance(example_id, str) or not example_id:
            raise ValueError("candidate example_id must be a nonempty string")
        if example_id in records_by_id:
            raise ValueError("duplicate candidate example_id")
        if "target_id" in record or "target" in record:
            raise ValueError("candidate records must not contain target identity")
        records_by_id[example_id] = record

    seen_examples: set[str] = set()
    seed_state_counts: Counter[tuple[int, str]] = Counter()
    family_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()

    for example in examples:
        if example.get("schema_version") != V4_EXAMPLE_SCHEMA_VERSION:
            raise ValueError("v4 example schema version does not match")
        if example.get("protocol_version") != V4_PROTOCOL_VERSION:
            raise ValueError("v4 example protocol version does not match")
        example_id = example.get("example_id")
        if not isinstance(example_id, str) or not example_id:
            raise ValueError("example_id must be a nonempty string")
        if example_id in seen_examples:
            raise ValueError("duplicate v4 example_id")
        seen_examples.add(example_id)
        if example_id not in records_by_id:
            raise ValueError("v4 example has no candidate record")

        seed = example.get("task_seed")
        state = example.get("screen_state")
        if not isinstance(seed, int) or seed not in V4_CALIBRATION_SEEDS:
            raise ValueError("task_seed is outside the v4 calibration seed set")
        if not isinstance(state, str) or state not in SCREEN_STATES:
            raise ValueError("screen_state is outside the frozen state set")
        expected_target = v4_calibration_target(seed, state)
        if (
            example.get("target_id") != expected_target.semantic_id
            or example.get("target") != expected_target.instruction
            or example.get("element_type") != expected_target.element_type
        ):
            raise ValueError("v4 calibration target does not match the frozen allocation")

        width = example.get("screen_width")
        height = example.get("screen_height")
        if width != CSS_WIDTH or height != CSS_HEIGHT:
            raise ValueError("v4 screenshot dimensions do not match the frozen protocol")
        validate_bbox(example.get("bbox"), width=width, height=height)

        candidates = records_by_id[example_id].get("candidates")
        if not isinstance(candidates, list):
            raise TypeError("v4 candidate list is missing")
        if len(candidates) != V4_EXPECTED_CANDIDATE_COUNT:
            raise ValueError("v4 candidate count does not match the frozen protocol")
        validate_candidate_set(candidates, width=width, height=height)
        candidate_ids = [candidate.get("semantic_id") for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("v4 candidate semantic IDs must be unique")
        if set(candidate_ids) != set(V4_CANDIDATE_IDS):
            raise ValueError("v4 candidate semantic IDs do not match the frozen set")
        if expected_target.semantic_id not in candidate_ids:
            raise ValueError("v4 target is missing from the target-independent candidates")

        seed_state_counts[(seed, state)] += 1
        state_counts[state] += 1
        target_counts[expected_target.semantic_id] += 1
        family_counts[V4_TARGET_FAMILIES[expected_target.semantic_id]] += 1

    if set(records_by_id) != seen_examples:
        raise ValueError("v4 candidate and example IDs do not match")
    if len(seed_state_counts) != expected_count or set(seed_state_counts.values()) != {1}:
        raise ValueError("v4 calibration must use every seed/state cell exactly once")
    if set(target_counts.values()) != {1} or len(target_counts) != expected_count:
        raise ValueError("every v4 target must appear exactly once")
    expected_families = {
        "relational_table": 4,
        "cross_panel_policy": 3,
        "recovery_state": 3,
    }
    if dict(family_counts) != expected_families:
        raise ValueError("v4 capability-family allocation must be exactly 4/3/3")

    return {
        "example_count": len(examples),
        "candidate_record_count": len(candidate_records),
        "screen_state_counts": dict(sorted(state_counts.items())),
        "target_counts": dict(sorted(target_counts.items())),
        "family_counts": dict(sorted(family_counts.items())),
        "seed_state_cell_count": len(seed_state_counts),
        "calibration_label": "CALIBRATION",
        "variant": "v4-pilot",
        "expected_candidate_count": V4_EXPECTED_CANDIDATE_COUNT,
    }


def _capture_one_pass(
    repository_root: Path,
    image_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('v4 capture requires `pip install -e ".[dev]"`') from exc

    from pixelgym.tasks.vendor_form.browser_contract import BROWSER_ARGS

    examples: list[dict[str, Any]] = []
    candidate_records: list[dict[str, Any]] = []
    browser_version = "unknown"
    image_dir.mkdir(parents=True, exist_ok=True)

    with local_v4_server() as base_url, sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(BROWSER_ARGS)}
        if executable.is_file():
            launch_options["executable_path"] = str(executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            raise RuntimeError("v4 capture requires the Playwright Chromium binary") from exc
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
        number = 0
        try:
            for seed in V4_CALIBRATION_SEEDS:
                for state in SCREEN_STATES:
                    number += 1
                    reset = _post_reset(base_url, seed)
                    page.goto(f"{base_url}/?seed={seed}", wait_until="networkidle")
                    page.locator(V4_READY_SELECTOR).wait_for(state="attached")
                    page.evaluate("() => document.fonts.ready")
                    _apply_v4_state(page, state=state, seed=seed)
                    instrumentation = page.evaluate(_V4_CANDIDATE_SCRIPT)
                    if (
                        instrumentation["css_width"] != CSS_WIDTH
                        or instrumentation["css_height"] != CSS_HEIGHT
                        or instrumentation["device_scale_factor"] != DEVICE_SCALE_FACTOR
                    ):
                        raise RuntimeError("browser viewport does not match the frozen protocol")
                    if len(instrumentation["candidates"]) != V4_EXPECTED_CANDIDATE_COUNT:
                        raise RuntimeError("v4 candidate count does not match the protocol")

                    image_path = image_dir / f"vendor-workbench-v4-{number:04d}.png"
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
                    validate_candidate_set(
                        candidates,
                        width=screen_width,
                        height=screen_height,
                    )
                    target = v4_calibration_target(seed, state)
                    target_candidate = next(
                        candidate
                        for candidate in candidates
                        if candidate["semantic_id"] == target.semantic_id
                    )
                    slug = target.semantic_id.replace("_", "-")
                    example_id = f"vendor-workbench-v4-{number:04d}-{slug}"
                    relative_image = image_path.relative_to(repository_root).as_posix()
                    examples.append(
                        {
                            "schema_version": V4_EXAMPLE_SCHEMA_VERSION,
                            "protocol_version": V4_PROTOCOL_VERSION,
                            "example_id": example_id,
                            "image_path": relative_image,
                            "image_sha256": _sha256(image_bytes),
                            "target_id": target.semantic_id,
                            "target": target.instruction,
                            "target_family": V4_TARGET_FAMILIES[target.semantic_id],
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
                    )
                    candidate_records.append(
                        {
                            "schema_version": V4_CANDIDATE_SCHEMA_VERSION,
                            "protocol_version": V4_PROTOCOL_VERSION,
                            "example_id": example_id,
                            "candidates": candidates,
                        }
                    )
        finally:
            context.close()
            browser.close()
    return examples, candidate_records, browser_version


def _generate_overlays(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    repository_root: Path,
) -> list[dict[str, Any]]:
    candidates_by_id = {row["example_id"]: row["candidates"] for row in candidate_records}
    marked_dir = repository_root / "artifacts" / "grounding-v4-pilot" / "images" / "marks"
    marked_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for number, example in enumerate(examples, start=1):
        raw_path = repository_root / example["image_path"]
        if _sha256(raw_path.read_bytes()) != example["image_sha256"]:
            raise ValueError("raw image digest changed after capture")
        candidates = candidates_by_id[example["example_id"]]
        with Image.open(raw_path) as raw_image:
            marked_image, marks = render_overlay(raw_image, candidates)
        marked_path = marked_dir / f"vendor-workbench-v4-{number:04d}.png"
        marked_image.save(marked_path, format="PNG", optimize=False, compress_level=9)
        target_proposed, target_mark_id = proposal_match(example["target_id"], marks)
        if not target_proposed:
            raise ValueError("v4 overlay does not propose the requested target")
        records.append(
            {
                "schema_version": V4_OVERLAY_SCHEMA_VERSION,
                "protocol_version": V4_PROTOCOL_VERSION,
                "example_id": example["example_id"],
                "raw_image_path": example["image_path"],
                "raw_image_sha256": example["image_sha256"],
                "marked_image_path": marked_path.relative_to(repository_root).as_posix(),
                "marked_image_sha256": _sha256(marked_path.read_bytes()),
                "screen_width": example["screen_width"],
                "screen_height": example["screen_height"],
                "marks": marks,
                "target_proposed": target_proposed,
                "target_mark_id": target_mark_id,
            }
        )
    return records


def _build_v4_contact_sheet(
    examples: list[dict[str, Any]],
    *,
    repository_root: Path,
    output_path: Path,
) -> None:
    """Render a readable two-column audit sheet for long compositional prompts."""
    columns = 2
    thumb_width, thumb_height, caption_height = 512, 384, 52
    rows = (len(examples) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + caption_height)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, example in enumerate(examples):
        with Image.open(repository_root / example["image_path"]) as source:
            image = source.convert("RGB")
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
        wrapped_target = "\n".join(textwrap.wrap(example["target"], width=78))
        caption = f"{example['example_id']}\n{wrapped_target}"
        draw.multiline_text(
            (origin_x + 4, origin_y + thumb_height + 3),
            caption,
            font=font,
            fill=(20, 20, 20),
            spacing=2,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=9)


def _build_v4_overlay_contact_sheet(
    records: list[dict[str, Any]],
    *,
    repository_root: Path,
    output_path: Path,
) -> None:
    columns = 2
    thumb_width, thumb_height, caption_height = 512, 384, 20
    rows = (len(records) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + caption_height)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, record in enumerate(records):
        with Image.open(repository_root / record["marked_image_path"]) as source:
            marked = source.convert("RGB")
        marked.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
        column, row = index % columns, index // columns
        origin_x = column * thumb_width
        origin_y = row * (thumb_height + caption_height)
        sheet.paste(marked, (origin_x, origin_y))
        draw.text(
            (origin_x + 4, origin_y + thumb_height + 3),
            record["example_id"],
            font=font,
            fill=(20, 20, 20),
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=9)


def capture_v4_calibration_dataset(repository_root: Path) -> dict[str, Any]:
    """Capture twice, verify bitwise equality, and write frozen v4 pilot inputs."""
    artifact_root = repository_root / "artifacts"
    pilot_root = artifact_root / "grounding-v4-pilot"
    primary_dir = pilot_root / "images" / "raw"
    repeat_dir = pilot_root / "images" / "raw-repeat"
    examples, candidates, browser_version = _capture_one_pass(repository_root, primary_dir)
    _capture_one_pass(repository_root, repeat_dir)
    repeatability = compare_png_directories(
        primary_dir,
        repeat_dir,
        reference_label="v4-pilot-pass-1",
        candidate_label="v4-pilot-pass-2",
    )
    summary = validate_v4_calibration_dataset(examples, candidates)
    overlays = _generate_overlays(examples, candidates, repository_root=repository_root)

    dataset_path = artifact_root / "grounding-v4-pilot-dataset.jsonl"
    candidates_path = artifact_root / "grounding-v4-pilot-candidates.jsonl"
    overlays_path = artifact_root / "grounding-v4-pilot-overlays.jsonl"
    dataset_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in examples),
        encoding="utf-8",
    )
    candidates_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in candidates),
        encoding="utf-8",
    )
    overlays_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in overlays),
        encoding="utf-8",
    )

    contact_sheet_path = pilot_root / "contact-sheet.png"
    marks_contact_sheet_path = pilot_root / "marks-contact-sheet.png"
    _build_v4_contact_sheet(
        examples,
        repository_root=repository_root,
        output_path=contact_sheet_path,
    )
    _build_v4_overlay_contact_sheet(
        overlays,
        repository_root=repository_root,
        output_path=marks_contact_sheet_path,
    )

    capture_evidence = {
        "schema_version": V4_CAPTURE_SCHEMA_VERSION,
        "protocol_version": V4_PROTOCOL_VERSION,
        "capture_version": CAPTURE_VERSION,
        "browser_engine": "chromium",
        "browser_version": browser_version,
        "calibration_seeds": list(V4_CALIBRATION_SEEDS),
        "screen_states": list(SCREEN_STATES),
        **summary,
        "repeatability": repeatability,
        "source_sha256": v4_capture_source_hashes(repository_root),
    }
    capture_path = artifact_root / "grounding-v4-pilot-capture.json"
    capture_path.write_text(
        json.dumps(capture_evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from pixelgym.grounding.evaluation import PARSER_VERSION_V2, PROMPT_VERSION_V2

    output_paths = {
        "dataset": dataset_path,
        "candidates": candidates_path,
        "overlays": overlays_path,
        "capture_evidence": capture_path,
        "contact_sheet": contact_sheet_path,
        "marks_contact_sheet": marks_contact_sheet_path,
    }
    manifest = {
        "schema_version": V4_MANIFEST_SCHEMA_VERSION,
        "protocol_version": V4_PROTOCOL_VERSION,
        "status": "calibration_captured_evaluation_not_run",
        "variant": "v4-pilot",
        "primary_model": "gpt-5.6-luna",
        "primary_parameters": {"reasoning_effort": "low", "temperature": None},
        "secondary_model": "claude-haiku-4-5-20251001",
        "prompt_version": PROMPT_VERSION_V2,
        "parser_version": PARSER_VERSION_V2,
        "calibration_seeds": list(V4_CALIBRATION_SEEDS),
        "calibration_example_count": summary["example_count"],
        "condition_call_cap": 20,
        "model_calls_performed": 0,
        "family_counts": summary["family_counts"],
        "target_specs": [
            {
                "semantic_id": spec.semantic_id,
                "instruction": spec.instruction,
                "element_type": spec.element_type,
                "family": V4_TARGET_FAMILIES[spec.semantic_id],
            }
            for spec in V4_TARGET_SPECS
        ],
        "calibration_targets": {
            "raw": "5-7/10",
            "marks": "6-8/10",
            "request_failures": 0,
            "parse_failures": 0,
        },
        "escalation_rule": (
            "within target band: request Haiku ceiling-check approval; "
            "above 0.85: design separate multi-step v4b; "
            "below 0.50 or any transport/parse failure: floor audit first"
        ),
        "decision_history": [],
        "outputs": {
            name: {
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
            for name, path in output_paths.items()
        },
    }
    manifest_path = artifact_root / "grounding-v4-pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
