from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pixelgym.platform.fingerprints import (
    build_dataset_manifest,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    verify_dataset_manifest,
)

Manifest = dict[str, Any]


@dataclass(frozen=True)
class _Snapshot:
    root: Path
    dataset_path: Path
    overlays_path: Path
    raw_image_path: Path
    marked_image_path: Path

    def build(self) -> tuple[Manifest, str]:
        return build_dataset_manifest(
            repository_root=self.root,
            dataset_path=self.dataset_path,
            overlays_path=self.overlays_path,
        )


def _read_jsonl(path: Path) -> list[Manifest]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _write_jsonl(path: Path, rows: list[Manifest]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _one_example_snapshot(
    repository_root: Path,
    root: Path,
    *,
    creation_order: tuple[str, ...] = ("raw", "marked", "dataset", "overlays"),
) -> _Snapshot:
    example = _read_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")[0]
    overlay = _read_jsonl(repository_root / "artifacts/grounding-overlays.jsonl")[0]
    raw_source = repository_root / example["image_path"]
    marked_source = repository_root / overlay["marked_image_path"]

    root.mkdir()
    raw_path = root / "images/raw/example.png"
    marked_path = root / "images/marked/example.png"
    dataset_path = root / "dataset.jsonl"
    overlays_path = root / "overlays.jsonl"
    example["image_path"] = str(raw_path.relative_to(root))
    overlay["raw_image_path"] = str(raw_path.relative_to(root))
    overlay["marked_image_path"] = str(marked_path.relative_to(root))

    def write_raw() -> None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_source.read_bytes())

    def write_marked() -> None:
        marked_path.parent.mkdir(parents=True, exist_ok=True)
        marked_path.write_bytes(marked_source.read_bytes())

    writers: dict[str, Callable[[], None]] = {
        "raw": write_raw,
        "marked": write_marked,
        "dataset": lambda: _write_jsonl(dataset_path, [example]),
        "overlays": lambda: _write_jsonl(overlays_path, [overlay]),
    }
    assert set(creation_order) == set(writers)
    for name in creation_order:
        writers[name]()
    return _Snapshot(root, dataset_path, overlays_path, raw_path, marked_path)


def _manifest_fingerprint(manifest: Manifest) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes(manifest))


def _mutate_record(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    examples = _read_jsonl(snapshot.dataset_path)
    examples[0]["screen_state"] = "text_field_focused"
    _write_jsonl(snapshot.dataset_path, examples)
    return snapshot.build()[0]


def _mutate_raw_image(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    _mutate_png(snapshot.raw_image_path)
    digest = sha256_file(snapshot.raw_image_path)
    examples = _read_jsonl(snapshot.dataset_path)
    overlays = _read_jsonl(snapshot.overlays_path)
    examples[0]["image_sha256"] = digest
    overlays[0]["raw_image_sha256"] = digest
    _write_jsonl(snapshot.dataset_path, examples)
    _write_jsonl(snapshot.overlays_path, overlays)
    return snapshot.build()[0]


def _mutate_marked_image(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    _mutate_png(snapshot.marked_image_path)
    overlays = _read_jsonl(snapshot.overlays_path)
    overlays[0]["marked_image_sha256"] = sha256_file(snapshot.marked_image_path)
    _write_jsonl(snapshot.overlays_path, overlays)
    return snapshot.build()[0]


def _mutate_png(path: Path) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    red, green, blue = image.getpixel((0, 0))
    image.putpixel((0, 0), ((red + 1) % 256, green, blue))
    image.save(path, format="PNG")


def _mutate_overlay_marks(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    overlays = _read_jsonl(snapshot.overlays_path)
    overlays[0]["marks"][0]["visible_label"] += " updated"
    _write_jsonl(snapshot.overlays_path, overlays)
    return snapshot.build()[0]


def _mutate_manifest_field(
    snapshot: _Snapshot,
    manifest: Manifest,
    *,
    field: str,
    value: object,
) -> Manifest:
    del snapshot
    mutated = copy.deepcopy(manifest)
    mutated[field] = value
    return mutated


def _mutate_protocol(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    return _mutate_manifest_field(
        snapshot,
        manifest,
        field="protocol_version",
        value=f"{manifest['protocol_version']}-mutated",
    )


def _mutate_capture(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    return _mutate_manifest_field(
        snapshot,
        manifest,
        field="capture_version",
        value=f"{manifest['capture_version']}-mutated",
    )


def _mutate_coordinate_convention(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    return _mutate_manifest_field(
        snapshot,
        manifest,
        field="coordinate_convention",
        value="one-based inclusive screenshot pixels",
    )


def _mutate_screen_dimensions(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del snapshot
    mutated = copy.deepcopy(manifest)
    mutated["screen"]["width"] += 1
    return mutated


Mutation = Callable[[_Snapshot, Manifest], Manifest]
IDENTITY_MUTATIONS: tuple[tuple[str, Mutation], ...] = (
    ("dataset-record", _mutate_record),
    ("raw-image-bytes-and-digest", _mutate_raw_image),
    ("marked-image-bytes-and-digest", _mutate_marked_image),
    ("overlay-marks", _mutate_overlay_marks),
    ("protocol-version", _mutate_protocol),
    ("capture-version", _mutate_capture),
    ("coordinate-convention", _mutate_coordinate_convention),
    ("screen-dimensions", _mutate_screen_dimensions),
)


@pytest.mark.parametrize(
    ("name", "mutate"), IDENTITY_MUTATIONS, ids=[row[0] for row in IDENTITY_MUTATIONS]
)
def test_identity_mutation_changes_authoritative_fingerprint(
    name: str,
    mutate: Mutation,
    repository_root: Path,
    tmp_path: Path,
) -> None:
    snapshot = _one_example_snapshot(repository_root, tmp_path / name)
    manifest, fingerprint = snapshot.build()

    mutated_manifest = mutate(snapshot, manifest)

    assert _manifest_fingerprint(mutated_manifest) != fingerprint


def _append_record(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del snapshot
    mutated = copy.deepcopy(manifest)
    appended = copy.deepcopy(mutated["examples"][0])
    appended["example_id"] += "-appended"
    mutated["examples"].append(appended)
    mutated["example_count"] += 1
    return mutated


def _remove_record(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del snapshot
    mutated = copy.deepcopy(manifest)
    mutated["examples"].pop()
    mutated["example_count"] -= 1
    return mutated


MANIFEST_TAMPERS: tuple[tuple[str, Mutation], ...] = IDENTITY_MUTATIONS + (
    ("appended-record", _append_record),
    ("removed-record", _remove_record),
)


@pytest.mark.parametrize(
    ("name", "tamper"), MANIFEST_TAMPERS, ids=[row[0] for row in MANIFEST_TAMPERS]
)
def test_manifest_tamper_is_rejected_under_original_fingerprint(
    name: str,
    tamper: Mutation,
    repository_root: Path,
    tmp_path: Path,
) -> None:
    snapshot = _one_example_snapshot(repository_root, tmp_path / name)
    manifest, fingerprint = snapshot.build()
    tampered_manifest = tamper(snapshot, manifest)

    with pytest.raises(ValueError, match="dataset manifest fingerprint mismatch"):
        verify_dataset_manifest(
            manifest=tampered_manifest,
            expected_fingerprint=fingerprint,
        )


def _set_tree_mtime(root: Path, timestamp_ns: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, ns=(timestamp_ns, timestamp_ns))
    os.utime(root, ns=(timestamp_ns, timestamp_ns))


def test_file_creation_order_and_modification_times_do_not_affect_identity(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    first = _one_example_snapshot(
        repository_root,
        tmp_path / "first",
        creation_order=("raw", "marked", "dataset", "overlays"),
    )
    second = _one_example_snapshot(
        repository_root,
        tmp_path / "second",
        creation_order=("overlays", "dataset", "marked", "raw"),
    )
    _set_tree_mtime(first.root, 1_600_000_000_000_000_000)
    _set_tree_mtime(second.root, 1_700_000_000_000_000_000)

    assert first.build()[1] == second.build()[1]


def test_directory_listing_order_does_not_affect_identity(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _one_example_snapshot(repository_root, tmp_path / "snapshot")
    expected = snapshot.build()[1]
    original_listdir = os.listdir
    original_iterdir = Path.iterdir

    monkeypatch.setattr(os, "listdir", lambda path: list(reversed(original_listdir(path))))
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda path: iter(reversed(list(original_iterdir(path)))),
    )

    assert snapshot.build()[1] == expected
