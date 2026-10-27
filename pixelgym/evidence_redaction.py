"""Shared redaction primitives for checked-in evidence records."""

from __future__ import annotations

import getpass
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

LOCAL_DEMO_SECRETS = (
    "local_demo_postgres_only",
    "local_demo_minio_only",
    "local-demo-csrf-secret-change-before-any-shared-use",
)

PathReplacement = tuple[Path, str]


def indexed_path_replacements(paths: Iterable[Path]) -> list[PathReplacement]:
    """Assign stable ``<path-N>`` placeholders in input order."""
    return [(path, f"<path-{index}>") for index, path in enumerate(paths)]


def standard_path_replacements(paths: Iterable[Path]) -> list[PathReplacement]:
    """Add self-describing home/temp placeholders to indexed custom paths."""
    replacements: list[PathReplacement] = []
    try:
        replacements.append((Path.home(), "<home>"))
    except RuntimeError:
        pass
    system_temp = Path(tempfile.gettempdir())
    # pytest's default basetemp is ``<system-temp>/pytest-of-<username>``, so the
    # username survives the plain temp-directory replacement one component deeper.
    # Longer replacements apply first, so this one wins over ``<system-temp>``.
    try:
        username = getpass.getuser()
    except (KeyError, ImportError, OSError):
        username = None
    if username:
        replacements.append(
            (system_temp / f"pytest-of-{username}", "<system-temp>/pytest-of-<user>")
        )
    replacements.append((system_temp, "<system-temp>"))
    replacements.extend(indexed_path_replacements(paths))
    return replacements


def _replace_path(value: str, source: str, replacement: str) -> str:
    # Do not replace a path inside a longer filename or path component. A slash
    # remains an allowed left boundary so file:///tmp still redacts /tmp.
    pattern = re.compile(rf"(?<![A-Za-z0-9._~-]){re.escape(source)}(?![A-Za-z0-9._~-])")
    return pattern.sub(lambda _match: replacement, value)


def redact_evidence_text(
    value: str,
    path_replacements: Sequence[PathReplacement],
    *,
    secrets: Sequence[str] = LOCAL_DEMO_SECRETS,
) -> str:
    """Redact literal/resolved paths at boundaries and known local secrets."""
    replacements: dict[str, str] = {}
    for path, replacement in path_replacements:
        replacements.setdefault(str(path), replacement)
        replacements.setdefault(str(path.resolve()), replacement)

    redacted = value
    for source, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        redacted = _replace_path(redacted, source, replacement)
    for secret in secrets:
        redacted = redacted.replace(secret, "<redacted-local-demo-secret>")
    return redacted
