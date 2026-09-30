"""Small filesystem and Git helpers shared by scripts and new code."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of ``path``'s bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any, *, indent: int | None = 2) -> None:
    """Write ``value`` as sorted-key JSON so ``path`` is either untouched or complete.

    The payload is serialized before any file is created, written to a temporary
    file in the destination directory, flushed, and renamed over ``path``. On
    any failure the temporary file is removed and ``path`` keeps its old bytes.
    """
    path = Path(path)
    encoded = (json.dumps(value, indent=indent, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def require_clean_worktree(root: Path, *, message: str = "tracked worktree must be clean") -> None:
    """Raise ``RuntimeError`` if any tracked file under ``root`` differs from HEAD."""
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status.strip():
        raise RuntimeError(f"{message}: {status.strip()}")
