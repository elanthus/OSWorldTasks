"""Shared strict serialization and repository-path helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Encode one canonical UTF-8 JSON identity representation."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load object-only UTF-8 JSONL, consistently ignoring blank lines."""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def resolve_repository_output(repository_root: Path, path_value: str) -> Path:
    """Resolve a data-supplied output path without permitting root escape."""
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("output path must be a nonempty repository-relative string")
    relative = Path(path_value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("output path must be a safe repository-relative path")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("output path escapes the repository root") from exc
    return resolved
