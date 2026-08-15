from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.platform.source_provenance import (
    SOURCE_PROVENANCE_SCHEMA_VERSION,
    SourceProvenance,
    load_packaged_source_provenance,
    source_tree_sha256,
)


def _write_manifest(root: Path, path: Path, *, state: str = "clean", digest: str | None = None) -> None:
    path.write_text(
        json.dumps(
            SourceProvenance(
                SOURCE_PROVENANCE_SCHEMA_VERSION,
                "a" * 40,
                digest or source_tree_sha256(root),
                state,
                "git-build-inputs-v1",
            ).to_dict()
        )
    )


def test_matching_clean_manifest_is_verified(repository_root: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "source-provenance.json"
    _write_manifest(repository_root, manifest)
    provenance = load_packaged_source_provenance(repository_root, manifest)
    assert provenance.state == "clean"
    assert provenance.revision == "a" * 40


@pytest.mark.parametrize("state", ["dirty", "unverifiable"])
def test_dirty_or_missing_provenance_is_not_clean(repository_root: Path, tmp_path: Path, state: str) -> None:
    manifest = tmp_path / "source-provenance.json"
    if state == "dirty":
        _write_manifest(repository_root, manifest, state=state)
    provenance = load_packaged_source_provenance(repository_root, manifest)
    assert provenance.state != "clean"


@pytest.mark.parametrize(
    "payload",
    [
        {"revision": "a" * 40},
        {
            "schema_version": SOURCE_PROVENANCE_SCHEMA_VERSION,
            "revision": "A" * 40,
            "source_tree_sha256": "b" * 64,
            "state": "clean",
            "verification_method": "git-build-inputs-v1",
        },
    ],
)
def test_missing_or_spoofed_revision_payload_is_unverifiable(
    repository_root: Path, tmp_path: Path, payload: dict[str, str]
) -> None:
    manifest = tmp_path / "source-provenance.json"
    manifest.write_text(json.dumps(payload))
    assert load_packaged_source_provenance(repository_root, manifest).state == "unverifiable"


def test_source_digest_mismatch_is_unverifiable(repository_root: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "source-provenance.json"
    _write_manifest(repository_root, manifest, digest="c" * 64)
    assert load_packaged_source_provenance(repository_root, manifest).state == "unverifiable"
