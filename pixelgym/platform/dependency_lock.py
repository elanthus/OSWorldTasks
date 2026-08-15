"""Validate and identify the exact Python 3.12 platform dependency lock.

This module intentionally uses only the standard library: the container calls it
before installing any third-party dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

PLATFORM_LOCK_RELATIVE_PATH = Path("requirements/platform-py312.lock")
_INPUT_DIGEST_PREFIX = "# pixelgym-platform-input-sha256: "
_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\;]+)(?:\s*;[^\\]+)?\s*(?:\\)?$")
_HASH = re.compile(r"^\s+--hash=sha256:[0-9a-f]{64}(?:\s+\\)?$")
_EXACT_DECLARATION = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)")


def _normalized_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        raise ValueError(f"unsupported project dependency declaration: {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _declared_exact_pins(requirements: list[str]) -> dict[str, str]:
    return {
        re.sub(r"[-_.]+", "-", match.group(1)).lower(): match.group(2)
        for requirement in requirements
        if (match := _EXACT_DECLARATION.match(requirement)) is not None
    }


def platform_input_sha256(pyproject_path: Path) -> str:
    """Hash the declared Python/platform inputs that the lock resolves."""
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    payload: dict[str, Any] = {
        "requires-python": project["requires-python"],
        "dependencies": project["dependencies"],
        "platform": project["optional-dependencies"]["platform"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dependency_lock_sha256(repository_root: Path) -> str:
    """Return the digest recorded in policy and run manifests."""
    lock_path = repository_root / PLATFORM_LOCK_RELATIVE_PATH
    verify_platform_lock(repository_root, lock_path)
    return hashlib.sha256(lock_path.read_bytes()).hexdigest()


def verify_platform_lock(repository_root: Path, lock_path: Path | None = None) -> None:
    """Fail closed unless the lock is complete, pinned, hashed, and current."""
    root = repository_root.resolve()
    pyproject_path = root / "pyproject.toml"
    target = lock_path or root / PLATFORM_LOCK_RELATIVE_PATH
    if not target.is_file():
        raise ValueError(f"platform dependency lock is missing: {target}")
    if not pyproject_path.is_file():
        raise ValueError(f"project metadata is missing: {pyproject_path}")

    lines = target.read_text(encoding="utf-8").splitlines()
    expected = platform_input_sha256(pyproject_path)
    declared = next((line.removeprefix(_INPUT_DIGEST_PREFIX) for line in lines if line.startswith(_INPUT_DIGEST_PREFIX)), None)
    if declared != expected:
        raise ValueError("platform dependency lock is stale or has an invalid platform-input digest")

    pins: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = _PIN.match(lines[index])
        if match is None:
            index += 1
            continue
        pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
        hashes = 0
        index += 1
        while index < len(lines) and lines[index].startswith((" ", "\t")):
            if _HASH.match(lines[index]):
                hashes += 1
            index += 1
        if hashes == 0:
            raise ValueError(f"platform dependency lock entry lacks a SHA-256 hash: {match.group(1)}")

    if not pins:
        raise ValueError("platform dependency lock contains no exact pins")
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    required = {_normalized_name(item) for item in project["dependencies"]}
    required.update(_normalized_name(item) for item in project["optional-dependencies"]["platform"])
    missing = sorted(required - pins.keys())
    if missing:
        raise ValueError("platform dependency lock omits declared runtime dependencies: " + ", ".join(missing))
    exact_pins = _declared_exact_pins(
        [*project["dependencies"], *project["optional-dependencies"]["platform"]]
    )
    mismatched = sorted(
        f"{name}=={expected} (lock has {pins[name]})"
        for name, expected in exact_pins.items()
        if pins.get(name) != expected
    )
    if mismatched:
        raise ValueError(
            "platform dependency lock versions do not match exact declared dependencies: "
            + ", ".join(mismatched)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the exact platform dependency lock")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    verify_platform_lock(args.repository_root)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"platform dependency lock verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
