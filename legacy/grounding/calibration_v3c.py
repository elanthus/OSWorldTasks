"""v3c Stage 0 calibration capture — data-table variant with repeated controls.

The v3c variant exercises a fundamentally different difficulty axis:
  - 8 identical Edit buttons (one per vendor row)
  - 8 identical Delete buttons (one per vendor row)
  - Disambiguation requires reading row context (vendor name, status, etc.)
  - 48 total candidates (vs 38 in v3b, 10 in v3a)

v3c is capture-only: never mounted by the task app or OSWorld adapter.
v3c uses the v3a point contract (x/y coordinates, not mark_id).
Table data is hardcoded (no generator) for full determinism.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from legacy.grounding.v3_server import V3C_READY_SELECTOR, local_v3c_server
from pixelgym.grounding.capture import (
    _candidate_record,
    _post_reset,
    build_contact_sheet,
)
from pixelgym.grounding.determinism import compare_png_directories
from pixelgym.grounding.overlays import (
    build_overlay_contact_sheet,
    proposal_match,
    render_overlay,
)
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
from pixelgym.serialization import canonical_json_text

V3C_PROTOCOL_VERSION = "pixelgym-grounding-v3c"
V3C_EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-v3c-example-v1"
V3C_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-v3c-candidates-v1"
V3C_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-v3c-overlay-v1"
V3C_CAPTURE_SCHEMA_VERSION = "pixelgym-grounding-v3c-capture-v1"
V3C_MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-v3c-manifest-v1"
V3C_CALIBRATION_SEEDS = (20, 21, 22, 23)
_V3C_CANDIDATE_RECORD_FIELDS = frozenset(
    {"schema_version", "protocol_version", "example_id", "candidates"}
)

V3C_TARGET_SPECS = (
    TargetSpec("edit_1", "Click the Edit button for Acme Industries", "button"),
    TargetSpec("edit_5", "Click the Edit button for Meridian Partners", "button"),
    TargetSpec("delete_3", "Click the Delete button for Pacific Trading Co", "button"),
    TargetSpec("delete_6", "Click the Delete button for Atlas Logistics", "button"),
    TargetSpec("name_2", "Click the GlobalTech Solutions vendor name", "link"),
    TargetSpec("name_7", "Click the Pinnacle Systems vendor name", "link"),
    TargetSpec("sort_status", "Click the Status column header to sort", "link"),
    TargetSpec("sort_country", "Click the Country column header to sort", "link"),
    TargetSpec("page_next", "Click the Next page button", "link"),
    TargetSpec("search_input", "Click the search input field", "text_input"),
)

V3C_EXPECTED_CANDIDATE_COUNT = 48


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_V3C_CAPTURE_SOURCE_PATHS = (
    "pixelgym/grounding/calibration_v3c.py",
    "pixelgym/grounding/capture.py",
    "pixelgym/grounding/determinism.py",
    "pixelgym/grounding/overlays.py",
    "pixelgym/grounding/v3_server.py",
    "pixelgym/grounding/schema.py",
    "pixelgym/tasks/vendor_form/browser_contract.py",
)
_V3C_CAPTURE_STATIC_ROOT = Path("pixelgym/grounding/v3c_app/static")


def v3c_capture_source_hashes(repository_root: Path) -> dict[str, str]:
    relative_paths = list(_V3C_CAPTURE_SOURCE_PATHS)
    relative_paths.extend(
        path.relative_to(repository_root).as_posix()
        for path in sorted((repository_root / _V3C_CAPTURE_STATIC_ROOT).rglob("*"))
        if path.is_file()
    )
    return {
        relative: _sha256((repository_root / relative).read_bytes())
        for relative in relative_paths
    }


def v3c_calibration_target(seed: int, screen_state: str) -> TargetSpec:
    """Return the crossed target for one v3c calibration seed/state capture."""
    if seed in TASK_SEEDS:
        raise ValueError("calibration seed must be outside the frozen TASK_SEEDS")
    if seed not in V3C_CALIBRATION_SEEDS:
        raise ValueError("seed is outside the v3c calibration seed set")
    try:
        state_index = SCREEN_STATES.index(screen_state)
    except ValueError as exc:
        raise ValueError("screen state is outside the frozen state set") from exc
    return V3C_TARGET_SPECS[(seed + state_index) % len(V3C_TARGET_SPECS)]


_V3C_CANDIDATE_SCRIPT = """
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
  // Header nav (5)
  add("nav_vendors", "link", "Vendors", document.querySelector("#nav-vendors"));
  add("nav_contracts", "link", "Contracts", document.querySelector("#nav-contracts"));
  add("nav_reports", "link", "Reports", document.querySelector("#nav-reports"));
  add("nav_settings", "link", "Settings", document.querySelector("#nav-settings"));
  add("nav_help", "link", "Help", document.querySelector("#nav-help"));
  // Sidebar (8)
  add("sb_dashboard", "link", "Dashboard", document.querySelector("#sb-dashboard"));
  add("sb_new_vendor", "link", "New Vendor", document.querySelector("#sb-new-vendor"));
  add("sb_vendor_list", "link", "Vendor List", document.querySelector("#sb-vendor-list"));
  add("sb_pending", "link", "Pending Review", document.querySelector("#sb-pending"));
  add("sb_import", "link", "Import Data", document.querySelector("#sb-import"));
  add("sb_export", "link", "Export Data", document.querySelector("#sb-export"));
  add("sb_audit", "link", "Audit Log", document.querySelector("#sb-audit"));
  add("sb_support", "link", "Support", document.querySelector("#sb-support"));
  // Search + Add button (2)
  add("search_input", "text_input", "Search vendors...", document.querySelector("#search-input"));
  add("add_vendor", "button", "Add New Vendor", document.querySelector("#add-vendor-button"));
  // Column sort headers (4)
  add("sort_name", "link", "Name", document.querySelector("#sort-name"));
  add("sort_status", "link", "Status", document.querySelector("#sort-status"));
  add("sort_email", "link", "Email", document.querySelector("#sort-email"));
  add("sort_country", "link", "Country", document.querySelector("#sort-country"));
  // Per-row controls: name link + Edit + Delete (3 x 8 = 24)
  for (let i = 1; i <= 8; i++) {
    const nameEl = document.querySelector("#name-" + i);
    add("name_" + i, "link", nameEl.textContent, nameEl);
    add("edit_" + i, "button", "Edit", document.querySelector("#edit-" + i));
    add("delete_" + i, "button", "Delete", document.querySelector("#delete-" + i));
  }
  // Pagination (5)
  add("page_prev", "link", "Previous", document.querySelector("#page-prev"));
  add("page_1", "link", "1", document.querySelector("#page-1"));
  add("page_2", "link", "2", document.querySelector("#page-2"));
  add("page_3", "link", "3", document.querySelector("#page-3"));
  add("page_next", "link", "Next", document.querySelector("#page-next"));
  return {
    css_width: window.innerWidth,
    css_height: window.innerHeight,
    device_scale_factor: window.devicePixelRatio,
    candidates: result,
  };
}
"""


def _apply_v3c_state(page: Any, state: str) -> None:
    """Drive the v3c data-table page into the requested screen state."""
    if state == "initial":
        return
    if state == "text_field_focused":
        page.locator("#search-input").focus()
        return
    if state == "validation_error":
        page.locator("#delete-1").click()
        expected = "Are you sure you want to delete Acme Industries?"
        if page.locator("#status-message").inner_text() != expected:
            raise RuntimeError("delete confirmation message mismatch")
        return
    if state == "partially_completed":
        page.locator("#search-input").fill("acme")
        page.locator("#search-input").focus()
        return
    if state == "completed_review":
        page.locator("#row-1").click()
        page.locator("#edit-1").focus()
        return
    raise ValueError(f"unknown screen state {state!r}")


def validate_v3c_calibration_dataset(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate v3c calibration-set structure: 4 seeds x 5 states = 20 examples."""
    expected_count = len(V3C_CALIBRATION_SEEDS) * len(SCREEN_STATES)
    if len(examples) != expected_count or len(candidate_records) != expected_count:
        raise ValueError(
            f"v3c calibration dataset must contain exactly {expected_count} examples"
        )

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for record in candidate_records:
        if set(record) != _V3C_CANDIDATE_RECORD_FIELDS:
            raise ValueError("v3c candidate record fields do not match")
        if record.get("schema_version") != V3C_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("v3c candidate schema version does not match")
        if record.get("protocol_version") != V3C_PROTOCOL_VERSION:
            raise ValueError("v3c candidate protocol version does not match")
        eid = record.get("example_id")
        if not isinstance(eid, str) or not eid:
            raise ValueError("candidate example_id must be a nonempty string")
        if eid in candidates_by_id:
            raise ValueError("duplicate candidate example_id")
        candidates_by_id[eid] = record

    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    seed_state_counts: Counter[tuple[int, str]] = Counter()

    for example in examples:
        if example.get("schema_version") != V3C_EXAMPLE_SCHEMA_VERSION:
            raise ValueError("v3c example schema version does not match")
        if example.get("protocol_version") != V3C_PROTOCOL_VERSION:
            raise ValueError("v3c protocol version does not match")

        seed = example.get("task_seed")
        state = example.get("screen_state")
        if type(seed) is not int or seed not in V3C_CALIBRATION_SEEDS:
            raise ValueError("task_seed is outside the v3c calibration seed set")
        if state not in SCREEN_STATES:
            raise ValueError("screen_state is outside the frozen state set")

        expected_target = v3c_calibration_target(seed, state)
        if (
            example.get("target_id") != expected_target.semantic_id
            or example.get("target") != expected_target.instruction
            or example.get("element_type") != expected_target.element_type
        ):
            raise ValueError(
                "v3c calibration target does not match the crossed allocation"
            )

        width = example.get("screen_width")
        height = example.get("screen_height")
        if type(width) is not int or type(height) is not int:
            raise ValueError("screen dimensions must be integers")
        validate_bbox(example.get("bbox"), width=width, height=height)

        eid = example["example_id"]
        if eid not in candidates_by_id:
            raise ValueError(f"no candidate record for example {eid!r}")
        candidate = candidates_by_id[eid]
        validate_candidate_set(candidate.get("candidates"), width=width, height=height)
        matches = [
            c
            for c in candidate["candidates"]
            if c["semantic_id"] == example["target_id"]
        ]
        if len(matches) != 1 or matches[0]["bbox"] != example["bbox"]:
            raise ValueError(
                "target box does not match its target-neutral candidate"
            )

        target_counts[example["target_id"]] += 1
        state_counts[state] += 1
        seed_state_counts[(seed, state)] += 1

    expected_seed_states = {
        (seed, state)
        for seed in V3C_CALIBRATION_SEEDS
        for state in SCREEN_STATES
    }
    if set(seed_state_counts) != expected_seed_states:
        raise ValueError(
            "v3c calibration must use every seed-by-state cell exactly once"
        )

    return {
        "example_count": len(examples),
        "candidate_record_count": len(candidate_records),
        "target_counts": dict(sorted(target_counts.items())),
        "screen_state_counts": dict(sorted(state_counts.items())),
        "seed_state_cell_count": len(seed_state_counts),
        "calibration_label": "CALIBRATION",
        "variant": "v3c",
        "expected_candidate_count": V3C_EXPECTED_CANDIDATE_COUNT,
    }


def _v3c_capture_one_pass(
    repository_root: Path,
    image_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Run one full Playwright capture pass over v3c calibration seeds."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            'v3c calibration capture requires `pip install -e ".[dev]"`'
        ) from exc

    from pixelgym.tasks.vendor_form.browser_contract import BROWSER_ARGS

    examples: list[dict[str, Any]] = []
    candidate_records: list[dict[str, Any]] = []
    browser_version = "unknown"
    image_dir.mkdir(parents=True, exist_ok=True)

    with local_v3c_server() as base_url, sync_playwright() as playwright:
        chromium_executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(BROWSER_ARGS)}
        if chromium_executable.is_file():
            launch_options["executable_path"] = str(chromium_executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            raise RuntimeError(
                "v3c capture requires the Playwright Chromium binary"
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
        number = 0
        try:
            for seed in V3C_CALIBRATION_SEEDS:
                for state in SCREEN_STATES:
                    number += 1
                    reset = _post_reset(base_url, seed)
                    page.goto(base_url, wait_until="networkidle")
                    page.locator(V3C_READY_SELECTOR).wait_for(state="attached")
                    page.evaluate("() => document.fonts.ready")
                    _apply_v3c_state(page, state)
                    if state == "initial":
                        page.evaluate(
                            "() => document.activeElement "
                            "&& document.activeElement.blur()"
                        )
                    instrumentation = page.evaluate(_V3C_CANDIDATE_SCRIPT)
                    if (
                        instrumentation["css_width"] != CSS_WIDTH
                        or instrumentation["css_height"] != CSS_HEIGHT
                        or instrumentation["device_scale_factor"] != DEVICE_SCALE_FACTOR
                    ):
                        raise RuntimeError(
                            "browser viewport does not match the frozen protocol"
                        )

                    actual_count = len(instrumentation["candidates"])
                    if actual_count != V3C_EXPECTED_CANDIDATE_COUNT:
                        raise RuntimeError(
                            f"v3c candidate count {actual_count} != "
                            f"expected {V3C_EXPECTED_CANDIDATE_COUNT}"
                        )

                    image_path = image_dir / f"vendor-list-v3c-cal-{number:04d}.png"
                    image_bytes = page.screenshot(type="png", animations="disabled")
                    image_path.write_bytes(image_bytes)
                    with Image.open(image_path) as decoded:
                        screen_width, screen_height = decoded.size

                    candidates = [
                        _candidate_record(
                            c,
                            screen_width=screen_width,
                            screen_height=screen_height,
                        )
                        for c in instrumentation["candidates"]
                    ]
                    validate_candidate_set(
                        candidates, width=screen_width, height=screen_height
                    )

                    target = v3c_calibration_target(seed, state)
                    target_candidate = next(
                        c
                        for c in candidates
                        if c["semantic_id"] == target.semantic_id
                    )
                    slug = target.semantic_id.replace("_", "-")
                    example_id = f"vendor-list-v3c-cal-{number:04d}-{slug}"
                    relative_image_path = image_path.relative_to(
                        repository_root
                    ).as_posix()

                    example = {
                        "schema_version": V3C_EXAMPLE_SCHEMA_VERSION,
                        "protocol_version": V3C_PROTOCOL_VERSION,
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
                    examples.append(example)
                    candidate_records.append(
                        {
                            "schema_version": V3C_CANDIDATE_SCHEMA_VERSION,
                            "protocol_version": V3C_PROTOCOL_VERSION,
                            "example_id": example_id,
                            "candidates": candidates,
                        }
                    )
        finally:
            context.close()
            browser.close()

    return examples, candidate_records, browser_version


def _generate_v3c_overlays(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    repository_root: Path,
) -> list[dict[str, Any]]:
    candidates_by_id = {r["example_id"]: r["candidates"] for r in candidate_records}
    marked_dir = (
        repository_root / "artifacts" / "grounding-v3c" / "images" / "marks"
    )
    marked_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for number, example in enumerate(examples, start=1):
        candidates = candidates_by_id[example["example_id"]]
        raw_path = repository_root / example["image_path"]
        raw_bytes = raw_path.read_bytes()
        if _sha256(raw_bytes) != example["image_sha256"]:
            raise ValueError("raw image digest changed after capture")
        with Image.open(raw_path) as raw_image:
            marked_image, marks = render_overlay(raw_image, candidates)
        marked_path = marked_dir / f"vendor-list-v3c-cal-{number:04d}.png"
        marked_image.save(
            marked_path, format="PNG", optimize=False, compress_level=9
        )
        marked_bytes = marked_path.read_bytes()

        target_proposed, target_mark_id = proposal_match(
            example["target_id"], marks
        )
        record = {
            "schema_version": V3C_OVERLAY_SCHEMA_VERSION,
            "protocol_version": V3C_PROTOCOL_VERSION,
            "example_id": example["example_id"],
            "raw_image_path": example["image_path"],
            "raw_image_sha256": example["image_sha256"],
            "marked_image_path": marked_path.relative_to(
                repository_root
            ).as_posix(),
            "marked_image_sha256": _sha256(marked_bytes),
            "screen_width": example["screen_width"],
            "screen_height": example["screen_height"],
            "marks": marks,
            "target_proposed": target_proposed,
            "target_mark_id": target_mark_id,
        }
        records.append(record)
    return records


def capture_v3c_calibration_dataset(repository_root: Path) -> dict[str, Any]:
    """Capture, verify, and write the v3c calibration dataset."""
    artifact_root = repository_root / "artifacts"
    primary_dir = artifact_root / "grounding-v3c" / "images" / "raw"
    repeat_dir = artifact_root / "grounding-v3c" / "images" / "raw-repeat"

    examples_1, candidates_1, browser_version = _v3c_capture_one_pass(
        repository_root, primary_dir
    )
    _, _, _ = _v3c_capture_one_pass(repository_root, repeat_dir)

    repeatability = compare_png_directories(
        primary_dir,
        repeat_dir,
        reference_label="v3c-calibration-pass-1",
        candidate_label="v3c-calibration-pass-2",
    )
    if (
        repeatability["byte_identical_file_count"] != repeatability["file_count"]
        or repeatability["differing_file_count"] != 0
    ):
        raise RuntimeError("v3c calibration capture was not bitwise repeatable")

    summary = validate_v3c_calibration_dataset(examples_1, candidates_1)

    overlays = _generate_v3c_overlays(
        examples_1, candidates_1, repository_root=repository_root
    )

    dataset_path = artifact_root / "grounding-v3c-calibration-dataset.jsonl"
    candidates_path = artifact_root / "grounding-v3c-calibration-candidates.jsonl"
    overlays_path = artifact_root / "grounding-v3c-calibration-overlays.jsonl"
    dataset_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in examples_1),
        encoding="utf-8",
    )
    candidates_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in candidates_1),
        encoding="utf-8",
    )
    overlays_path.write_text(
        "".join(canonical_json_text(row) + "\n" for row in overlays),
        encoding="utf-8",
    )

    contact_sheet_path = artifact_root / "grounding-v3c" / "contact-sheet.png"
    marks_contact_sheet_path = (
        artifact_root / "grounding-v3c" / "marks-contact-sheet.png"
    )
    build_contact_sheet(
        examples_1,
        repository_root=repository_root,
        output_path=contact_sheet_path,
    )
    build_overlay_contact_sheet(
        overlays,
        repository_root=repository_root,
        output_path=marks_contact_sheet_path,
    )

    source_hashes = v3c_capture_source_hashes(repository_root)

    capture_evidence = {
        "schema_version": V3C_CAPTURE_SCHEMA_VERSION,
        "protocol_version": V3C_PROTOCOL_VERSION,
        "capture_version": CAPTURE_VERSION,
        "browser_engine": "chromium",
        "browser_version": browser_version,
        "calibration_seeds": list(V3C_CALIBRATION_SEEDS),
        "screen_states": list(SCREEN_STATES),
        **summary,
        "repeatability": repeatability,
        "source_sha256": source_hashes,
    }
    capture_path = artifact_root / "grounding-v3c-capture.json"
    capture_path.write_text(
        json.dumps(capture_evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from pixelgym.grounding.evaluation import (
        PARSER_VERSION_V2,
        PROMPT_VERSION_V2,
    )

    manifest = {
        "schema_version": V3C_MANIFEST_SCHEMA_VERSION,
        "protocol_version": V3C_PROTOCOL_VERSION,
        "status": "calibration_captured_evaluation_not_run",
        "variant": "v3c",
        "difficulty_levers": [
            "8 identical Edit buttons (row-context disambiguation)",
            "8 identical Delete buttons (row-context disambiguation)",
            "48 total candidates",
            "header + sidebar occlusion",
        ],
        "design": (
            f"{len(V3C_CALIBRATION_SEEDS)} calibration seeds x "
            f"{len(SCREEN_STATES)} screen states; "
            "crossed target allocation; "
            "one target per screenshot"
        ),
        "prompt_version": PROMPT_VERSION_V2,
        "parser_version": PARSER_VERSION_V2,
        "calibration_seeds": list(V3C_CALIBRATION_SEEDS),
        "target_specs": [
            {
                "semantic_id": spec.semantic_id,
                "instruction": spec.instruction,
                "element_type": spec.element_type,
            }
            for spec in V3C_TARGET_SPECS
        ],
        "scored_example_count": 0,
        "calibration_example_count": summary["example_count"],
        "escalation_rule": (
            "0.60 <= m <= 0.85: freeze v3c; "
            "m > 0.85: report saturation; "
            "m < 0.60: floor check then report honestly"
        ),
        "decision_history": [],
        "model_calls_performed": 0,
        "outputs": {
            "dataset": {
                "path": dataset_path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(dataset_path.read_bytes()),
            },
            "candidates": {
                "path": candidates_path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(candidates_path.read_bytes()),
            },
            "overlays": {
                "path": overlays_path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(overlays_path.read_bytes()),
            },
            "capture_evidence": {
                "path": capture_path.relative_to(repository_root).as_posix(),
                "sha256": _sha256(capture_path.read_bytes()),
            },
            "contact_sheet": {
                "path": contact_sheet_path.relative_to(
                    repository_root
                ).as_posix(),
                "sha256": _sha256(contact_sheet_path.read_bytes()),
            },
            "marks_contact_sheet": {
                "path": marks_contact_sheet_path.relative_to(
                    repository_root
                ).as_posix(),
                "sha256": _sha256(marks_contact_sheet_path.read_bytes()),
            },
        },
    }
    manifest_path = artifact_root / "grounding-v3c-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
