from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pixelgym.platform.dependency_lock import (
    PLATFORM_LOCK_RELATIVE_PATH,
    dependency_lock_sha256,
    platform_input_sha256,
    verify_platform_lock,
)


def _write_project(root: Path, *, platform_requirement: str = "platform-lib==2.0") -> None:
    (root / "pyproject.toml").write_text(
        """[project]
name = "fixture"
requires-python = ">=3.12"
dependencies = ["base-lib>=1"]

[project.optional-dependencies]
platform = [""" + repr(platform_requirement) + "]\n"
    )


def _write_lock(root: Path, entries: list[str] | None = None) -> Path:
    lock = root / PLATFORM_LOCK_RELATIVE_PATH
    lock.parent.mkdir()
    digest = platform_input_sha256(root / "pyproject.toml")
    lock.write_text(
        "# pixelgym-platform-input-sha256: " + digest + "\n"
        + "\n".join(
            entries
            or [
                "base-lib==1.0 \\",
                "    --hash=sha256:" + "a" * 64,
                "platform-lib==2.0 \\",
                "    --hash=sha256:" + "b" * 64,
            ]
        )
        + "\n"
    )
    return lock


def test_repository_lock_is_exact_hashed_and_is_the_manifest_digest(repository_root: Path) -> None:
    lock = repository_root / PLATFORM_LOCK_RELATIVE_PATH
    verify_platform_lock(repository_root)
    assert dependency_lock_sha256(repository_root) == hashlib.sha256(lock.read_bytes()).hexdigest()
    text = lock.read_text()
    assert "mlflow==3.14.0" in text
    assert "metaflow==2.19.35" in text
    assert "boto3==1.40.1" in text
    assert "psycopg2-binary==2.9.10" in text
    assert "--hash=sha256:" in text


def test_platform_container_verifies_and_installs_only_the_exact_lock(repository_root: Path) -> None:
    dockerfile = (repository_root / "deploy/Dockerfile.platform").read_text()
    assert "COPY requirements/platform-py312.lock ./requirements/platform-py312.lock" in dockerfile
    assert "dependency_lock.py --repository-root /app" in dockerfile
    assert "pip install --no-cache-dir --require-hashes -r requirements/platform-py312.lock" in dockerfile
    assert "pip install --no-cache-dir --no-deps ." in dockerfile
    assert ".[platform]" not in dockerfile


def test_missing_lock_fails_closed(tmp_path: Path) -> None:
    _write_project(tmp_path)
    with pytest.raises(ValueError, match="lock is missing"):
        verify_platform_lock(tmp_path)


def test_modified_lock_without_hash_fails_closed(tmp_path: Path) -> None:
    _write_project(tmp_path)
    _write_lock(tmp_path, ["base-lib==1.0", "platform-lib==2.0 \\", "    --hash=sha256:" + "b" * 64])
    with pytest.raises(ValueError, match="lacks a SHA-256 hash"):
        verify_platform_lock(tmp_path)


def test_stale_lock_inputs_fail_closed(tmp_path: Path) -> None:
    _write_project(tmp_path)
    _write_lock(tmp_path)
    _write_project(tmp_path, platform_requirement="platform-lib==3.0")
    with pytest.raises(ValueError, match="stale"):
        verify_platform_lock(tmp_path)


def test_inconsistent_lock_omitting_declared_dependency_fails_closed(tmp_path: Path) -> None:
    _write_project(tmp_path)
    _write_lock(tmp_path, ["base-lib==1.0 \\", "    --hash=sha256:" + "a" * 64])
    with pytest.raises(ValueError, match="omits declared runtime dependencies: platform-lib"):
        verify_platform_lock(tmp_path)
