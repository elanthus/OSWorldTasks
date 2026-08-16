"""Build-bound source provenance for platform evaluation runs.

The manifest is generated in a Git checkout before an image is built.  At runtime
we verify its source digest against the files packaged into that image; a revision
string supplied in an environment variable is never evidence by itself.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

SOURCE_PROVENANCE_SCHEMA_VERSION = "pixelgym-source-provenance-v1"
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
# These are the repository inputs copied by deploy/Dockerfile.platform.  Keep this
# list explicit: provenance must not silently expand to local build by-products.
SOURCE_INPUT_PATHS = (
    "pyproject.toml",
    "README.md",
    "requirements/platform-py312.lock",
    "pixelgym",
    "flows",
    "config",
    "scripts",
    "artifacts/grounding-dataset.jsonl",
    "artifacts/grounding-overlays.jsonl",
    "artifacts/grounding-predictions.jsonl",
    "artifacts/grounding",
)
_PYTHON_BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})
_FAILURE_REASONS = frozenset(
    {
        "not_configured",
        "manifest_missing",
        "manifest_not_regular_file",
        "manifest_read_failed",
        "manifest_invalid_utf8",
        "manifest_malformed_json",
        "manifest_schema_invalid",
        "revision_invalid",
        "source_digest_mismatch",
        "source_verification_failed",
    }
)
_LOGGER = logging.getLogger(__name__)


def _is_packaged_source_file(item: Path, root: Path) -> bool:
    """Return whether ``item`` is a source input rather than Python bytecode.

    Docker receives the same source directories, but bytecode and ``__pycache__``
    are local interpreter output rather than source.  Excluding only those files
    keeps the digest sensitive to every other copied file and fail-closed for a
    missing declared input.
    """
    relative = item.relative_to(root)
    return "__pycache__" not in relative.parts and item.suffix not in _PYTHON_BYTECODE_SUFFIXES


@dataclass(frozen=True)
class SourceProvenance:
    schema_version: str
    revision: str | None
    source_tree_sha256: str | None
    state: str
    verification_method: str
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"clean", "dirty", "unverifiable"}:
            raise ValueError("source provenance state must be clean, dirty, or unverifiable")
        if self.state == "unverifiable":
            if self.revision is not None or self.source_tree_sha256 is not None:
                raise ValueError("unverifiable provenance must not claim a revision or source digest")
            if self.verification_method != "none":
                raise ValueError("unverifiable provenance must use verification method none")
            if self.failure_reason not in _FAILURE_REASONS:
                raise ValueError("unverifiable provenance must include a recognized failure reason")
            return
        if self.failure_reason is not None:
            raise ValueError("verified provenance must not include a failure reason")
        if not self.revision or not _REVISION_RE.fullmatch(self.revision):
            raise ValueError("source provenance revision must be a lowercase 40-character Git commit")
        if not self.source_tree_sha256 or not re.fullmatch(r"[0-9a-f]{64}", self.source_tree_sha256):
            raise ValueError("source provenance digest must be a lowercase SHA-256 digest")
        if self.verification_method != "git-build-inputs-v1":
            raise ValueError("source provenance verification method is unsupported")

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def source_tree_sha256(repository_root: Path) -> str:
    """Digest exactly the files copied into the platform image, by path and bytes."""
    root = repository_root.resolve()
    entries: list[dict[str, str]] = []
    for relative in SOURCE_INPUT_PATHS:
        path = root / relative
        if not path.exists():
            raise ValueError(f"packaged source input is missing: {relative}")
        paths = [path] if path.is_file() else (item for item in path.rglob("*") if item.is_file())
        for item in paths:
            if not _is_packaged_source_file(item, root):
                continue
            entries.append(
                {
                    "path": item.relative_to(root).as_posix(),
                    "sha256": sha256_bytes(item.read_bytes()),
                }
            )
    return sha256_bytes(canonical_json_bytes(sorted(entries, key=lambda entry: entry["path"])))


def generate_source_provenance(repository_root: Path) -> SourceProvenance:
    """Derive revision and clean/dirty state from the checked-out Git worktree."""
    root = repository_root.resolve()
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Git revision and worktree state could not be verified") from exc
    return SourceProvenance(
        schema_version=SOURCE_PROVENANCE_SCHEMA_VERSION,
        revision=revision,
        source_tree_sha256=source_tree_sha256(root),
        state="clean" if not status else "dirty",
        verification_method="git-build-inputs-v1",
    )


def write_source_provenance(repository_root: Path, output: Path) -> SourceProvenance:
    provenance = generate_source_provenance(repository_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(provenance.to_dict()) + b"\n")
    return provenance


def load_packaged_source_provenance(repository_root: Path, path: Path | None) -> SourceProvenance:
    """Return verified packaged provenance or a safe, actionable unverified result.

    Failure reasons are a small public enum: they are safe for persisted run evidence,
    operator logs, and the control-plane UI without exposing a local path or manifest data.
    """

    def unverifiable(reason: str) -> SourceProvenance:
        _LOGGER.warning("packaged source provenance is unverifiable: %s", reason)
        return SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION, None, None, "unverifiable", "none", reason
        )

    if path is None:
        return unverifiable("not_configured")
    try:
        if not path.exists():
            return unverifiable("manifest_missing")
        if not path.is_file():
            return unverifiable("manifest_not_regular_file")
    except OSError:
        return unverifiable("manifest_read_failed")
    try:
        value = json.loads(path.read_text())
    except OSError:
        return unverifiable("manifest_read_failed")
    except UnicodeDecodeError:
        return unverifiable("manifest_invalid_utf8")
    except json.JSONDecodeError:
        return unverifiable("manifest_malformed_json")
    try:
        if not isinstance(value, dict):
            return unverifiable("manifest_schema_invalid")
        revision = value.get("revision")
        if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
            return unverifiable("revision_invalid")
        provenance = SourceProvenance(**value)
    except (TypeError, ValueError):
        return unverifiable("manifest_schema_invalid")
    try:
        if provenance.source_tree_sha256 != source_tree_sha256(repository_root):
            return unverifiable("source_digest_mismatch")
        return provenance
    except OSError:
        return unverifiable("source_verification_failed")
    except ValueError:
        return unverifiable("source_verification_failed")
