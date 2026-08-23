"""v3a Stage 0 calibration capture — seeds 20–23 over the unchanged vendor-form app.

This module is a parameterized entry point over the existing capture.py machinery.
Its underscore-prefixed capture imports are an intentional build-time dependency pinned by
the provenance hash; they are not part of the evaluation adapter.
It does NOT modify TASK_SEEDS, any frozen v1/v2 artifact, or any Sprint 1–2 source
file.  Calibration examples are labelled CALIBRATION and are never part of the scored
100-example benchmark.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from pixelgym.grounding.capture import (
    _CANDIDATE_SCRIPT,
    _apply_state,
    _candidate_record,
    _post_reset,
    build_contact_sheet,
    capture_source_hashes,
    local_capture_server,
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
    TARGET_SPECS,
    TASK_SEEDS,
    TargetSpec,
    validate_bbox,
    validate_candidate_set,
)
from pixelgym.serialization import canonical_json_text
from pixelgym.tasks.vendor_form import generator

V3A_PROTOCOL_VERSION = "pixelgym-grounding-v3a"
CALIBRATION_EXAMPLE_SCHEMA_VERSION = "pixelgym-grounding-calibration-example-v1"
CALIBRATION_CANDIDATE_SCHEMA_VERSION = "pixelgym-grounding-calibration-candidates-v1"
CALIBRATION_OVERLAY_SCHEMA_VERSION = "pixelgym-grounding-calibration-overlay-v1"
CALIBRATION_CAPTURE_SCHEMA_VERSION = "pixelgym-grounding-calibration-capture-v1"
CALIBRATION_MANIFEST_SCHEMA_VERSION = "pixelgym-grounding-v3a-manifest-v1"
CALIBRATION_SEEDS = (20, 21, 22, 23)
_CALIBRATION_CANDIDATE_RECORD_FIELDS = frozenset(
    {"schema_version", "protocol_version", "example_id", "candidates"}
)
_V3A_EXTRA_CAPTURE_SOURCE_PATHS = (
    "pixelgym/grounding/calibration_v3a.py",
    "pixelgym/grounding/determinism.py",
    "pixelgym/grounding/overlays.py",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def calibration_capture_source_hashes(repository_root: Path) -> dict[str, str]:
    """Include v3a orchestration and shared evidence code in capture provenance."""
    hashes = capture_source_hashes(repository_root)
    hashes.update(
        {
            relative: _sha256((repository_root / relative).read_bytes())
            for relative in _V3A_EXTRA_CAPTURE_SOURCE_PATHS
        }
    )
    return dict(sorted(hashes.items()))


def calibration_target(seed: int, screen_state: str) -> TargetSpec:
    """Return the crossed target for one calibration seed/state capture.

    Uses the same allocation rule as v2: ``TARGET_SPECS[(seed + state_index) % 10]``.
    Rejects seeds inside the frozen ``TASK_SEEDS`` to prevent mixing calibration
    with scored examples.
    """
    if seed in TASK_SEEDS:
        raise ValueError("calibration seed must be outside the frozen TASK_SEEDS")
    try:
        state_index = SCREEN_STATES.index(screen_state)
    except ValueError as exc:
        raise ValueError("screen state is outside the frozen state set") from exc
    return TARGET_SPECS[(seed + state_index) % len(TARGET_SPECS)]


def compare_calibration_non_image_evidence(
    reference: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare canonical task and target-neutral candidate evidence across passes."""
    reference_text = canonical_json_text(reference)
    candidate_text = canonical_json_text(candidate)
    reference_sha256 = _sha256(reference_text.encode("utf-8"))
    candidate_sha256 = _sha256(candidate_text.encode("utf-8"))
    if reference_text != candidate_text:
        raise RuntimeError("v3a calibration non-image evidence was not repeatable")

    task_evidence: dict[int, dict[str, Any]] = {}
    candidate_records = []
    for record in reference:
        task_evidence[int(record["task_seed"])] = {
            "task_id": record["task_id"],
            "canonical_task_sha256": record["canonical_task_sha256"],
        }
        candidate_records.append(record["candidate_record"])
    return {
        "schema_version": "pixelgym-grounding-non-image-repeatability-v1",
        "record_count": len(reference),
        "matched": True,
        "reference_aggregate_sha256": reference_sha256,
        "candidate_aggregate_sha256": candidate_sha256,
        "candidate_records_sha256": _sha256(
            canonical_json_text(candidate_records).encode("utf-8")
        ),
        "tasks": [
            {"task_seed": seed, **task_evidence[seed]}
            for seed in sorted(task_evidence)
        ],
    }


def validate_calibration_dataset(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate calibration-set structure: 4 seeds × 5 states = 20 examples."""
    expected_count = len(CALIBRATION_SEEDS) * len(SCREEN_STATES)
    if len(examples) != expected_count or len(candidate_records) != expected_count:
        raise ValueError(f"calibration dataset must contain exactly {expected_count} examples")

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for record in candidate_records:
        if set(record) != _CALIBRATION_CANDIDATE_RECORD_FIELDS:
            raise ValueError("calibration candidate record fields do not match")
        if record.get("schema_version") != CALIBRATION_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("calibration candidate schema version does not match")
        if record.get("protocol_version") != V3A_PROTOCOL_VERSION:
            raise ValueError("calibration candidate protocol version does not match")
        eid = record.get("example_id")
        if not isinstance(eid, str) or not eid:
            raise ValueError("candidate example_id must be a nonempty string")
        if eid in candidates_by_id:
            raise ValueError("duplicate candidate example_id")
        candidates_by_id[eid] = record

    target_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    seed_state_counts: Counter[tuple[int, str]] = Counter()
    cell_counts: Counter[tuple[str, str]] = Counter()

    for example in examples:
        if example.get("schema_version") != CALIBRATION_EXAMPLE_SCHEMA_VERSION:
            raise ValueError("calibration example schema version does not match")
        if example.get("protocol_version") != V3A_PROTOCOL_VERSION:
            raise ValueError("calibration protocol version does not match")

        seed = example.get("task_seed")
        state = example.get("screen_state")
        if type(seed) is not int or seed not in CALIBRATION_SEEDS:
            raise ValueError("task_seed is outside the calibration seed set")
        if state not in SCREEN_STATES:
            raise ValueError("screen_state is outside the frozen state set")

        expected_target = calibration_target(seed, state)
        if (
            example.get("target_id") != expected_target.semantic_id
            or example.get("target") != expected_target.instruction
            or example.get("element_type") != expected_target.element_type
        ):
            raise ValueError("calibration target does not match the crossed allocation")

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
            c for c in candidate["candidates"]
            if c["semantic_id"] == example["target_id"]
        ]
        if len(matches) != 1 or matches[0]["bbox"] != example["bbox"]:
            raise ValueError("target box does not match its target-neutral candidate")

        target_counts[example["target_id"]] += 1
        state_counts[state] += 1
        seed_state_counts[(seed, state)] += 1
        cell_counts[(example["target_id"], state)] += 1

    expected_seed_states = {
        (seed, state) for seed in CALIBRATION_SEEDS for state in SCREEN_STATES
    }
    if set(seed_state_counts) != expected_seed_states:
        raise ValueError("calibration must use every seed-by-state cell exactly once")
    if any(count != 1 for count in seed_state_counts.values()):
        raise ValueError("duplicate seed-by-state cell")

    return {
        "example_count": len(examples),
        "candidate_record_count": len(candidate_records),
        "target_counts": dict(sorted(target_counts.items())),
        "screen_state_counts": dict(sorted(state_counts.items())),
        "seed_state_cell_count": len(seed_state_counts),
        "target_state_cell_count": len(cell_counts),
        "calibration_label": "CALIBRATION",
    }


def _capture_one_pass(
    repository_root: Path,
    image_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Run one full Playwright capture pass over calibration seeds.

    Returns (examples, candidate_records, browser_version, non_image_evidence).
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('calibration capture requires `pip install -e ".[dev]"`') from exc

    from pixelgym.tasks.vendor_form.browser_contract import BROWSER_ARGS, READY_SELECTOR

    examples: list[dict[str, Any]] = []
    candidate_records: list[dict[str, Any]] = []
    non_image_evidence: list[dict[str, Any]] = []
    browser_version = "unknown"
    image_dir.mkdir(parents=True, exist_ok=True)

    with local_capture_server() as base_url, sync_playwright() as playwright:
        chromium_executable = Path(playwright.chromium.executable_path)
        launch_options: dict[str, Any] = {"headless": True, "args": list(BROWSER_ARGS)}
        if chromium_executable.is_file():
            launch_options["executable_path"] = str(chromium_executable)
        try:
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            raise RuntimeError(
                "calibration capture requires the Playwright Chromium binary; run "
                "`python -m playwright install chromium` after installing the dev extra"
            ) from exc
        browser_version = browser.version
        try:
            context = browser.new_context(
                viewport={"width": CSS_WIDTH, "height": CSS_HEIGHT},
                device_scale_factor=DEVICE_SCALE_FACTOR,
                locale="en-US",
                timezone_id="UTC",
                color_scheme="light",
                reduced_motion="reduce",
            )
            page = context.new_page()
        except BaseException:
            browser.close()
            raise
        number = 0
        try:
            for seed in CALIBRATION_SEEDS:
                for state in SCREEN_STATES:
                    number += 1
                    reset = _post_reset(base_url, seed)
                    page.goto(base_url, wait_until="networkidle")
                    page.locator(READY_SELECTOR).wait_for(state="attached")
                    page.evaluate("() => document.fonts.ready")
                    task = page.evaluate(
                        "() => fetch('/api/task').then(response => response.json())"
                    )
                    expected_task = generator.generate_task(seed)
                    observed_task = {
                        "task_id": task["task_id"],
                        "schema_version": task["schema_version"],
                        "seed": task["seed"],
                        "fields": task["fields"],
                        "options": task["options"],
                    }
                    if observed_task != expected_task or reset["task_id"] != task["task_id"]:
                        raise RuntimeError("capture server task does not match its canonical spec")
                    canonical_task_json = generator.canonical_json(
                        {key: value for key, value in observed_task.items() if key != "task_id"}
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
                        raise RuntimeError(
                            "browser viewport does not match the frozen protocol"
                        )

                    image_path = image_dir / f"vendor-form-v3a-cal-{number:04d}.png"
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

                    target = calibration_target(seed, state)
                    target_candidate = next(
                        (c for c in candidates if c["semantic_id"] == target.semantic_id),
                        None,
                    )
                    if target_candidate is None:
                        raise LookupError(
                            f"missing calibration target candidate {target.semantic_id!r}"
                        )
                    slug = target.semantic_id.replace("_", "-")
                    example_id = f"vendor-form-v3a-cal-{number:04d}-{slug}"
                    relative_image_path = image_path.relative_to(
                        repository_root
                    ).as_posix()

                    example = {
                        "schema_version": CALIBRATION_EXAMPLE_SCHEMA_VERSION,
                        "protocol_version": V3A_PROTOCOL_VERSION,
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
                    candidate_record = {
                        "schema_version": CALIBRATION_CANDIDATE_SCHEMA_VERSION,
                        "protocol_version": V3A_PROTOCOL_VERSION,
                        "example_id": example_id,
                        "candidates": candidates,
                    }
                    candidate_records.append(candidate_record)
                    non_image_evidence.append(
                        {
                            "example_id": example_id,
                            "task_seed": seed,
                            "task_id": task["task_id"],
                            "canonical_task_json": canonical_task_json,
                            "canonical_task_sha256": _sha256(
                                canonical_task_json.encode("utf-8")
                            ),
                            "candidate_record": candidate_record,
                        }
                    )
        finally:
            context.close()
            browser.close()

    return examples, candidate_records, browser_version, non_image_evidence


def _generate_calibration_overlays(
    examples: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Render set-of-marks overlays using the unchanged target-agnostic pipeline."""
    candidates_by_id = {r["example_id"]: r["candidates"] for r in candidate_records}
    marked_dir = (
        repository_root / "artifacts" / "grounding-v3a" / "images" / "marks"
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
        marked_path = marked_dir / f"vendor-form-v3a-cal-{number:04d}.png"
        marked_image.save(
            marked_path, format="PNG", optimize=False, compress_level=9
        )
        marked_bytes = marked_path.read_bytes()

        target_proposed, target_mark_id = proposal_match(
            example["target_id"], marks
        )
        record = {
            "schema_version": CALIBRATION_OVERLAY_SCHEMA_VERSION,
            "protocol_version": V3A_PROTOCOL_VERSION,
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


def capture_calibration_dataset(
    repository_root: Path, *, write_manifest: bool = True
) -> dict[str, Any]:
    """Capture, verify, and write the v3a calibration dataset.

    Performs two independent capture passes and checks bitwise repeatability
    before writing final artifacts.
    """
    artifact_root = repository_root / "artifacts"
    primary_dir = artifact_root / "grounding-v3a" / "images" / "raw"
    repeat_dir = artifact_root / "grounding-v3a" / "images" / "raw-repeat"

    examples_1, candidates_1, browser_version, non_image_1 = _capture_one_pass(
        repository_root, primary_dir
    )
    _, _, _, non_image_2 = _capture_one_pass(repository_root, repeat_dir)

    repeatability = compare_png_directories(
        primary_dir,
        repeat_dir,
        reference_label="calibration-pass-1",
        candidate_label="calibration-pass-2",
    )
    if (
        repeatability["byte_identical_file_count"] != repeatability["file_count"]
        or repeatability["differing_file_count"] != 0
    ):
        raise RuntimeError("v3a calibration capture was not bitwise repeatable")
    non_image_repeatability = compare_calibration_non_image_evidence(
        non_image_1, non_image_2
    )

    summary = validate_calibration_dataset(examples_1, candidates_1)

    overlays = _generate_calibration_overlays(
        examples_1, candidates_1, repository_root=repository_root
    )

    dataset_path = artifact_root / "grounding-v3a-calibration-dataset.jsonl"
    candidates_path = artifact_root / "grounding-v3a-calibration-candidates.jsonl"
    overlays_path = artifact_root / "grounding-v3a-calibration-overlays.jsonl"
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

    contact_sheet_path = artifact_root / "grounding-v3a" / "contact-sheet.png"
    marks_contact_sheet_path = (
        artifact_root / "grounding-v3a" / "marks-contact-sheet.png"
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

    source_hashes = calibration_capture_source_hashes(repository_root)

    capture_evidence = {
        "schema_version": CALIBRATION_CAPTURE_SCHEMA_VERSION,
        "protocol_version": V3A_PROTOCOL_VERSION,
        "capture_version": CAPTURE_VERSION,
        "browser_engine": "chromium",
        "browser_version": browser_version,
        "calibration_seeds": list(CALIBRATION_SEEDS),
        "screen_states": list(SCREEN_STATES),
        **summary,
        "repeatability": repeatability,
        "non_image_repeatability": non_image_repeatability,
        "source_sha256": source_hashes,
        "dataset_path": dataset_path.relative_to(repository_root).as_posix(),
        "candidates_path": candidates_path.relative_to(repository_root).as_posix(),
        "overlays_path": overlays_path.relative_to(repository_root).as_posix(),
        "contact_sheet_path": contact_sheet_path.relative_to(
            repository_root
        ).as_posix(),
        "marks_contact_sheet_path": marks_contact_sheet_path.relative_to(
            repository_root
        ).as_posix(),
    }
    capture_path = artifact_root / "grounding-v3a-capture.json"
    capture_path.write_text(
        json.dumps(capture_evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from pixelgym.grounding.evaluation import (
        PARSER_VERSION_V2,
        PROMPT_VERSION_V2,
    )

    manifest = {
        "schema_version": CALIBRATION_MANIFEST_SCHEMA_VERSION,
        "protocol_version": V3A_PROTOCOL_VERSION,
        "status": "calibration_captured_evaluation_not_run",
        "design": (
            f"{len(CALIBRATION_SEEDS)} calibration seeds x "
            f"{len(SCREEN_STATES)} screen states; "
            "crossed target allocation; "
            "one target per screenshot"
        ),
        "prompt_version": PROMPT_VERSION_V2,
        "parser_version": PARSER_VERSION_V2,
        "calibration_seeds": list(CALIBRATION_SEEDS),
        "scored_example_count": 0,
        "calibration_example_count": summary["example_count"],
        "escalation_rule": (
            "0.60 <= m <= 0.85: freeze v3a; "
            "m > 0.85: report and request v3b entry; "
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
    manifest_path = artifact_root / "grounding-v3a-manifest.json"
    if write_manifest:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return manifest
