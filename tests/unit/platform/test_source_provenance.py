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


def _write_packaged_source_tree(root: Path, *, reverse_creation_order: bool = False) -> None:
    files = {
        "pyproject.toml": "[project]\nname = 'fixture'\n",
        "README.md": "fixture\n",
        "requirements/platform-py312.lock": "fixture-lock\n",
        "pixelgym/platform/module.py": "VALUE = 1\n",
        "flows/evaluation.py": "FLOW = 1\n",
        "config/policy.json": "{}\n",
        "scripts/run.py": "print('run')\n",
        "artifacts/grounding-dataset.jsonl": "{}\n",
        "artifacts/grounding-overlays.jsonl": "{}\n",
        "artifacts/grounding-predictions.jsonl": "{}\n",
        "artifacts/grounding/record.json": "{}\n",
    }
    items = list(files.items())
    if reverse_creation_order:
        items.reverse()
    for relative, content in items:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


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


def test_source_digest_ignores_nested_python_bytecode_and_cache_artifacts(tmp_path: Path) -> None:
    _write_packaged_source_tree(tmp_path)
    before = source_tree_sha256(tmp_path)
    cache = tmp_path / "pixelgym/platform/nested/__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-312.pyc").write_bytes(b"nested cache")
    (tmp_path / "flows/evaluation.pyc").write_bytes(b"bytecode")
    (tmp_path / "scripts/run.pyo").write_bytes(b"optimized bytecode")
    assert source_tree_sha256(tmp_path) == before


def test_source_digest_changes_for_real_packaged_source_change(tmp_path: Path) -> None:
    _write_packaged_source_tree(tmp_path)
    before = source_tree_sha256(tmp_path)
    (tmp_path / "pixelgym/platform/module.py").write_text("VALUE = 2\n")
    assert source_tree_sha256(tmp_path) != before


def test_source_digest_is_independent_of_filesystem_creation_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_packaged_source_tree(first)
    _write_packaged_source_tree(second, reverse_creation_order=True)
    assert source_tree_sha256(first) == source_tree_sha256(second)


def test_dockerignore_excludes_bytecode_without_excluding_platform_build_inputs(repository_root: Path) -> None:
    patterns = {
        line.strip()
        for line in (repository_root / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {"**/__pycache__/", "*.pyc", "*.pyo"} <= patterns
    assert not {
        "pyproject.toml",
        "README.md",
        "requirements/",
        "requirements/platform-py312.lock",
        "pixelgym/",
        "flows/",
        "config/",
        "scripts/",
        "artifacts/",
    } & patterns
