"""Capture a stratified browser screenshot sample for the human D5.4 review."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from playwright.sync_api import Page, sync_playwright

from pixelgym.grounding.v5.contracts import Partition, StageKind, WorkflowFamily
from pixelgym.grounding.v5.generator import tasks_for_partition
from pixelgym.grounding.v5.server import READY_SELECTOR, local_v5_server
from pixelgym.serialization import canonical_json_bytes


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture(page: Page, output: Path, task: Any, label: str) -> dict[str, Any]:
    filename = f"{task.family.value}-{label}.png"
    path = output / filename
    page.screenshot(path=str(path))
    return {
        "task_id": task.task_id,
        "family": task.family.value,
        "stage_label": label,
        "image_path": filename,
        "image_sha256": _sha256(path),
        "screen": {"width": 1024, "height": 768},
    }


def _advance(page: Page, stage: Any) -> None:
    if stage.kind is StageKind.TEXT:
        field = page.locator('[data-control-id="text_input"]')
        field.click()
        field.fill(stage.required_text)
        page.locator('[data-control-id="continue"]').click()
        return
    page.locator(f'[data-control-id="{stage.target_control_id}"]').click()
    if stage.recovery_stage:
        page.locator('[data-control-id="repair_implicated"]').click()


def _contact_sheet(output: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    thumbnail_size = (512, 384)
    columns = 3
    rows = (len(records) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 512, rows * 424), "white")
    draw = ImageDraw.Draw(sheet)
    for index, record in enumerate(records):
        with Image.open(output / record["image_path"]) as image:
            thumbnail = image.convert("RGB").resize(thumbnail_size)
        x = (index % columns) * 512
        y = (index // columns) * 424
        sheet.paste(thumbnail, (x, y))
        draw.text((x + 8, y + 390), f"{record['family']} · {record['stage_label']}", fill="black")
    path = output / "contact-sheet.png"
    sheet.save(path, format="PNG", optimize=False)
    return {"image_path": path.name, "image_sha256": _sha256(path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        raise RuntimeError("development sample output directory must be fresh")
    args.output_directory.mkdir(parents=True)
    tasks = tasks_for_partition(Partition.DEVELOPMENT)
    selected = [next(task for task in tasks if task.family is family) for family in WorkflowFamily]
    records: list[dict[str, Any]] = []
    with local_v5_server() as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        try:
            for task in selected:
                page.goto(f"{origin}/?seed={task.seed}", wait_until="networkidle")
                page.wait_for_selector(READY_SELECTOR, state="attached")
                records.append(_capture(page, args.output_directory, task, "initial"))
                for index, stage in enumerate(task.stages):
                    if index == 5:
                        records.append(
                            _capture(page, args.output_directory, task, "deferred-consumer")
                        )
                    if index == len(task.stages) - 2:
                        records.append(_capture(page, args.output_directory, task, "review"))
                    if stage.recovery_stage:
                        page.locator(f'[data-control-id="{stage.target_control_id}"]').click()
                        records.append(
                            _capture(page, args.output_directory, task, "visible-recovery")
                        )
                        page.locator('[data-control-id="repair_implicated"]').click()
                    else:
                        _advance(page, stage)
        finally:
            browser.close()
    manifest = {
        "schema_version": "pixelgym-agent-v5-development-sample-v1",
        "purpose": "human usability review; not human-performance evidence",
        "task_count": len(selected),
        "screenshot_count": len(records),
        "records": records,
        "contact_sheet": _contact_sheet(args.output_directory, records),
        "provider_calls_made": 0,
    }
    (args.output_directory / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")


if __name__ == "__main__":
    main()
