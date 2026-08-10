from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.prepare_osworld_docker import (
    GUEST_ARTIFACT_NAME,
    PreparationError,
    extract_guest_image,
)


def _archive(path: Path, body: bytes) -> Path:
    expected_name = GUEST_ARTIFACT_NAME.removesuffix(".zip")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(expected_name, body)
    return path


def test_extract_guest_image_accepts_cached_image_matching_verified_member(tmp_path):
    body = b"small deterministic guest image"
    archive = _archive(tmp_path / "guest.zip", body)
    destination = tmp_path / GUEST_ARTIFACT_NAME.removesuffix(".zip")
    destination.write_bytes(body)

    assert extract_guest_image(archive, tmp_path) == destination


def test_extract_guest_image_rejects_same_size_corrupt_cached_image(tmp_path):
    body = b"small deterministic guest image"
    archive = _archive(tmp_path / "guest.zip", body)
    destination = tmp_path / GUEST_ARTIFACT_NAME.removesuffix(".zip")
    destination.write_bytes(b"X" * len(body))

    with pytest.raises(PreparationError, match="size/CRC validation"):
        extract_guest_image(archive, tmp_path)
