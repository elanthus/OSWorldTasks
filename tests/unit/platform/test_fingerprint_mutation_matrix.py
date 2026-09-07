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

_EXAMPLE_COUNT = 2


@dataclass(frozen=True)
class _Snapshot:
    root: Path
    dataset_path: Path
    overlays_path: Path
    raw_image_path: Path
    marked_image_path: Path
    primary_example_id: str

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


def _find_by_example_id(rows: list[Manifest], example_id: str) -> Manifest:
    for row in rows:
        if row["example_id"] == example_id:
            return row
    raise AssertionError(f"no record for example_id {example_id!r}")


def _dataset_snapshot(
    repository_root: Path,
    root: Path,
    *,
    example_count: int = _EXAMPLE_COUNT,
    creation_order: tuple[str, ...] = ("raw", "marked", "dataset", "overlays"),
) -> _Snapshot:
    source_examples = _read_jsonl(repository_root / "artifacts/grounding-dataset.jsonl")
    overlay_by_id = {
        row["example_id"]: row
        for row in _read_jsonl(repository_root / "artifacts/grounding-overlays.jsonl")
    }
    examples = [copy.deepcopy(row) for row in source_examples[:example_count]]
    overlays = [copy.deepcopy(overlay_by_id[row["example_id"]]) for row in examples]

    root.mkdir()
    dataset_path = root / "dataset.jsonl"
    overlays_path = root / "overlays.jsonl"

    raw_sources: list[Path] = []
    marked_sources: list[Path] = []
    raw_targets: list[Path] = []
    marked_targets: list[Path] = []
    for index, (example, overlay) in enumerate(zip(examples, overlays, strict=True)):
        raw_sources.append(repository_root / example["image_path"])
        marked_sources.append(repository_root / overlay["marked_image_path"])
        raw_path = root / f"images/raw/example-{index}.png"
        marked_path = root / f"images/marked/example-{index}.png"
        example["image_path"] = str(raw_path.relative_to(root))
        overlay["raw_image_path"] = str(raw_path.relative_to(root))
        overlay["marked_image_path"] = str(marked_path.relative_to(root))
        raw_targets.append(raw_path)
        marked_targets.append(marked_path)

    def write_raw() -> None:
        for raw_path, raw_source in zip(raw_targets, raw_sources, strict=True):
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw_source.read_bytes())

    def write_marked() -> None:
        for marked_path, marked_source in zip(marked_targets, marked_sources, strict=True):
            marked_path.parent.mkdir(parents=True, exist_ok=True)
            marked_path.write_bytes(marked_source.read_bytes())

    writers: dict[str, Callable[[], None]] = {
        "raw": write_raw,
        "marked": write_marked,
        "dataset": lambda: _write_jsonl(dataset_path, examples),
        "overlays": lambda: _write_jsonl(overlays_path, overlays),
    }
    assert set(creation_order) == set(writers)
    for name in creation_order:
        writers[name]()
    return _Snapshot(
        root,
        dataset_path,
        overlays_path,
        raw_targets[0],
        marked_targets[0],
        examples[0]["example_id"],
    )


def _manifest_fingerprint(manifest: Manifest) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes(manifest))


def _mutate_record(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    examples = _read_jsonl(snapshot.dataset_path)
    record = _find_by_example_id(examples, snapshot.primary_example_id)
    assert record["screen_state"] == "initial"
    record["screen_state"] = "validation_error"
    _write_jsonl(snapshot.dataset_path, examples)
    return snapshot.build()[0]


def _mutate_raw_image(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    _mutate_png(snapshot.raw_image_path)
    digest = sha256_file(snapshot.raw_image_path)
    examples = _read_jsonl(snapshot.dataset_path)
    overlays = _read_jsonl(snapshot.overlays_path)
    _find_by_example_id(examples, snapshot.primary_example_id)["image_sha256"] = digest
    _find_by_example_id(overlays, snapshot.primary_example_id)["raw_image_sha256"] = digest
    _write_jsonl(snapshot.dataset_path, examples)
    _write_jsonl(snapshot.overlays_path, overlays)
    return snapshot.build()[0]


def _mutate_marked_image(snapshot: _Snapshot, manifest: Manifest) -> Manifest:
    del manifest
    _mutate_png(snapshot.marked_image_path)
    overlays = _read_jsonl(snapshot.overlays_path)
    _find_by_example_id(overlays, snapshot.primary_example_id)["marked_image_sha256"] = (
        sha256_file(snapshot.marked_image_path)
    )
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
    overlay = _find_by_example_id(overlays, snapshot.primary_example_id)
    overlay["marks"][0]["visible_label"] += " updated"
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
        value=f"{manifest['coordinate_convention']}; mutated",
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
    snapshot = _dataset_snapshot(repository_root, tmp_path / name)
    manifest, fingerprint = snapshot.build()

    # Anchor the test-local fingerprint reimplementation against the manifest's own
    # (unmutated) fingerprint before trusting it to judge the mutated manifest below.
    assert _manifest_fingerprint(manifest) == fingerprint

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
    snapshot = _dataset_snapshot(repository_root, tmp_path / name)
    manifest, fingerprint = snapshot.build()
    tampered_manifest = tamper(snapshot, manifest)

    # appended/removed-record tampers must still leave at least one surviving,
    # untouched entry behind so the test proves tamper detection, not emptiness.
    assert len(tampered_manifest["examples"]) >= 1

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
    first = _dataset_snapshot(
        repository_root,
        tmp_path / "first",
        creation_order=("raw", "marked", "dataset", "overlays"),
    )
    second = _dataset_snapshot(
        repository_root,
        tmp_path / "second",
        creation_order=("overlays", "dataset", "marked", "raw"),
    )
    first_timestamp_ns = 1_600_000_000_000_000_000
    second_timestamp_ns = 1_700_000_000_000_000_000
    _set_tree_mtime(first.root, first_timestamp_ns)
    _set_tree_mtime(second.root, second_timestamp_ns)

    # Prove os.utime actually took effect rather than silently no-oping.
    assert first.dataset_path.stat().st_mtime_ns == first_timestamp_ns
    assert second.dataset_path.stat().st_mtime_ns == second_timestamp_ns
    assert first.dataset_path.stat().st_mtime_ns != second.dataset_path.stat().st_mtime_ns

    assert first.build()[1] == second.build()[1]


def test_directory_listing_order_does_not_affect_identity(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _dataset_snapshot(repository_root, tmp_path / "snapshot")
    expected = snapshot.build()[1]
    original_listdir = os.listdir
    original_iterdir = Path.iterdir
    calls = {"listdir": 0, "iterdir": 0}

    def reversed_listdir(path: str = ".") -> list[str]:
        calls["listdir"] += 1
        return list(reversed(original_listdir(path)))

    def reversed_iterdir(path: Path) -> Any:
        calls["iterdir"] += 1
        return iter(reversed(list(original_iterdir(path))))

    monkeypatch.setattr(os, "listdir", reversed_listdir)
    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)

    assert snapshot.build()[1] == expected
    # If a future implementation starts enumerating directories to build identity,
    # these reversed patches would flip the fingerprint and this assertion would catch it.
    assert calls == {"listdir": 0, "iterdir": 0}, "identity never consults directory enumeration"
