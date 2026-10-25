"""Owner-fillable freeze draft for the unrun v2 crossed grounding evaluation.

The v2 protocol requires the provider, exact model, prompt version, parameters, pilot,
call cap, and approval to be frozen before the first model call.  This module records
everything that can be derived from checked-in evidence and leaves the owner-decided
fields null.  It reads metadata only: no image bytes, no network, no provider call.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.evaluation import (
    V2_PILOT_SELECTION_RULE,
    _load_inputs,
    pilot_example_ids,
)
from pixelgym.grounding.protocols import V2_PROTOCOL

FREEZE_DRAFT_SCHEMA_VERSION = "pixelgym-grounding-v2-freeze-draft-v1"
DEFAULT_FREEZE_DRAFT_PATH = "artifacts/grounding-v2-freeze-draft.json"
PROTOCOL_DOCUMENT_PATH = "artifacts/grounding-v2-protocol.md"
PILOT_EXAMPLE_COUNT = 10
PILOT_CONDITION_CALL_CAP = 20
FULL_CONDITION_CALL_CAP_AFTER_PILOT = 180
TOTAL_CONDITION_CALL_CAP = PILOT_CONDITION_CALL_CAP + FULL_CONDITION_CALL_CAP_AFTER_PILOT

_INPUT_LABELS = ("dataset", "candidates", "overlays")
_DRAFT_NOTES = (
    (
        "No model has been called under protocol v2. Owner fills provider, model, "
        "prompt_version, parser_version, prediction_schema_version, parameters, and "
        "approval before the first call."
    ),
    (
        "The pilot runs only after approval and stops at its cap; the full run needs a "
        "second approval and must not exceed full_new_calls_after_pilot new calls."
    ),
    ("V2 results are reported separately from v1 and never replace the frozen v1 result files."),
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_v2_freeze_draft(repository_root: Path) -> dict[str, Any]:
    """Derive the draft from the frozen v2 manifest, metadata, and pilot rule."""
    protocol = V2_PROTOCOL
    assert protocol.manifest_path is not None
    manifest_path = repository_root / protocol.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol_version") != protocol.protocol_version:
        raise ValueError("v2 manifest protocol version does not match the selected protocol")
    if manifest.get("model_calls_performed") != 0:
        raise ValueError("v2 manifest reports model calls; refusing to draft a fresh freeze")

    relative_paths = {
        "dataset": protocol.dataset_path,
        "candidates": protocol.candidates_path,
        "overlays": protocol.overlays_path,
    }
    inputs: dict[str, dict[str, str]] = {}
    for label in _INPUT_LABELS:
        relative = relative_paths[label]
        digest = _sha256_file(repository_root / relative)
        expected = manifest["outputs"][label]
        if expected.get("path") != relative or expected.get("sha256") != digest:
            raise ValueError(f"v2 {label} digest does not match the frozen v2 manifest")
        inputs[label] = {"path": relative, "sha256": digest}

    examples, _overlays = _load_inputs(repository_root, protocol)
    pilot_ids = pilot_example_ids(examples, protocol=protocol)
    if len(pilot_ids) != PILOT_EXAMPLE_COUNT or len(set(pilot_ids)) != PILOT_EXAMPLE_COUNT:
        raise ValueError("v2 pilot selection must name ten distinct examples")
    example_count = len(examples)
    if example_count * 2 != TOTAL_CONDITION_CALL_CAP:
        raise ValueError("v2 condition call cap does not equal two calls per example")

    return {
        "schema_version": FREEZE_DRAFT_SCHEMA_VERSION,
        "status": "draft_awaiting_owner_freeze",
        "protocol_version": protocol.protocol_version,
        "protocol_document": PROTOCOL_DOCUMENT_PATH,
        "manifest_path": protocol.manifest_path,
        "manifest_sha256": _sha256_file(manifest_path),
        "inputs": inputs,
        "example_count": example_count,
        "paired_condition_call_count": example_count * 2,
        "conditions": ["raw", "marks"],
        "pilot": {
            "example_count": PILOT_EXAMPLE_COUNT,
            "condition_call_count": PILOT_CONDITION_CALL_CAP,
            "selection_rule": V2_PILOT_SELECTION_RULE,
            "example_ids": pilot_ids,
        },
        "condition_call_cap": {
            "pilot_new_calls": PILOT_CONDITION_CALL_CAP,
            "full_new_calls_after_pilot": FULL_CONDITION_CALL_CAP_AFTER_PILOT,
            "total": TOTAL_CONDITION_CALL_CAP,
        },
        "provider": None,
        "model": None,
        "prompt_version": None,
        "parser_version": None,
        "prediction_schema_version": None,
        "parameters": None,
        "approval": {"owner": None, "approved_utc": None, "pilot_approved": None},
        "output_paths": {
            "pilot_predictions": protocol.pilot_predictions_path,
            "full_predictions": protocol.full_predictions_path,
            "response_cache": protocol.cache_directory,
        },
        "model_calls_performed": 0,
        "notes": list(_DRAFT_NOTES),
    }


def write_v2_freeze_draft(repository_root: Path, path: Path | None = None) -> dict[str, Any]:
    """Write the draft once; an existing file (possibly owner-edited) is left untouched."""
    target = path or repository_root / DEFAULT_FREEZE_DRAFT_PATH
    draft = build_v2_freeze_draft(repository_root)
    written = False
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written = True
    return {
        "freeze_draft_path": (
            target.relative_to(repository_root).as_posix()
            if target.is_relative_to(repository_root)
            else str(target)
        ),
        "written": written,
        "existing_file_preserved": not written,
        "pilot_example_ids": draft["pilot"]["example_ids"],
        "condition_call_cap": draft["condition_call_cap"],
        "model_calls_performed": 0,
    }
