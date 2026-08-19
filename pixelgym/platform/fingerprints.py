"""Canonical content identities for platform datasets and artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pixelgym import serialization as _serialization
from pixelgym.grounding.schema import validate_example
from pixelgym.serialization import load_jsonl

canonical_json_bytes = _serialization.canonical_json_bytes

DATASET_MANIFEST_SCHEMA = "pixelgym-grounding-dataset-manifest-v1"
COORDINATE_CONVENTION = "zero-based screenshot pixels; half-open target boxes [x0,y0,x1,y1)"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset(root: Path, path_value: str, expected_sha256: str, *, role: str) -> dict[str, Any]:
    path = Path(path_value)
    resolved = path if path.is_absolute() else root / path
    if not resolved.is_file():
        raise ValueError(f"missing {role} asset")
    actual = sha256_file(resolved)
    if actual != expected_sha256:
        raise ValueError(f"{role} asset digest mismatch")
    return {"role": role, "sha256": actual, "size": resolved.stat().st_size}


def _identity_record(record: dict[str, Any], ignored_paths: Iterable[str]) -> dict[str, Any]:
    result = dict(record)
    for key in ignored_paths:
        result.pop(key, None)
    return result


def build_dataset_manifest(
    *,
    repository_root: Path,
    dataset_path: Path,
    overlays_path: Path,
) -> tuple[dict[str, Any], str]:
    """Validate a frozen dataset and return its path-independent canonical identity."""
    examples = load_jsonl(dataset_path)
    overlays = load_jsonl(overlays_path)
    if not examples:
        raise ValueError("dataset must not be empty")
    for example in examples:
        validate_example(example)
    example_ids = [row["example_id"] for row in examples]
    if len(set(example_ids)) != len(example_ids):
        raise ValueError("dataset contains duplicate example IDs")
    overlay_by_id = {row.get("example_id"): row for row in overlays}
    if len(overlay_by_id) != len(overlays) or set(overlay_by_id) != set(example_ids):
        raise ValueError("overlay metadata must match dataset examples exactly")

    entries: list[dict[str, Any]] = []
    for example in sorted(examples, key=lambda row: row["example_id"]):
        overlay = overlay_by_id[example["example_id"]]
        raw_asset = _asset(
            repository_root,
            example["image_path"],
            example["image_sha256"],
            role="raw_image",
        )
        marked_asset = _asset(
            repository_root,
            overlay["marked_image_path"],
            overlay["marked_image_sha256"],
            role="marked_image",
        )
        record_identity = _identity_record(example, ("image_path",))
        overlay_identity = _identity_record(overlay, ("marked_image_path", "raw_image_path"))
        # Privileged target metadata participates in the frozen build-time dataset identity but is
        # never included in a served policy or rendered control-plane view.
        entries.append(
            {
                "example_id": example["example_id"],
                "record_sha256": sha256_bytes(canonical_json_bytes(record_identity)),
                "overlay_sha256": sha256_bytes(canonical_json_bytes(overlay_identity)),
                "assets": [raw_asset, marked_asset],
            }
        )
    first = examples[0]
    manifest = {
        "schema_version": DATASET_MANIFEST_SCHEMA,
        "protocol_version": first["protocol_version"],
        "capture_version": first["capture_version"],
        "coordinate_convention": COORDINATE_CONVENTION,
        "screen": {"width": first["screen_width"], "height": first["screen_height"]},
        "example_count": len(entries),
        "examples": entries,
    }
    return manifest, "sha256:" + sha256_bytes(canonical_json_bytes(manifest))


def verify_dataset_manifest(
    *, manifest: dict[str, Any], expected_fingerprint: str
) -> None:
    actual = "sha256:" + sha256_bytes(canonical_json_bytes(manifest))
    if actual != expected_fingerprint:
        raise ValueError("dataset manifest fingerprint mismatch")
