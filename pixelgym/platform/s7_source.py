"""Git-byte and installed-runtime bindings for the local scripted S7 packages."""

from __future__ import annotations

import importlib.metadata
import io
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import _files, _hash, write_private
from pixelgym.platform.policy_subprocess import PolicyWorkerSpec
from pixelgym.serialization import canonical_json_bytes

SOURCE_PATHS = (
    "pixelgym",
    "scripts",
    "requirements",
    "pyproject.toml",
    "docs/s7-serving-demo-proposal.md",
)
ENDPOINT = "http://127.0.0.1:9"


def git_source(root: Path) -> tuple[str, dict[str, bytes]]:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("Git is required for S7 source provenance")

    def git(*args: str) -> bytes:
        return subprocess.run(
            [str(Path(executable).resolve()), *args],
            cwd=root,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout

    if Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() != root.resolve():
        raise ValueError("S7 source must be the exact Git checkout root")
    if git("status", "--porcelain", "--untracked-files=all", "--", *SOURCE_PATHS).strip():
        raise ValueError(
            "S7 selected source must be committed and clean, including untracked files"
        )
    revision = git("rev-parse", "HEAD").decode().strip()
    result = {}
    with tarfile.open(fileobj=io.BytesIO(git("archive", revision, "--", *SOURCE_PATHS))) as tar:
        for entry in tar:
            if entry.isdir():
                continue
            stream = tar.extractfile(entry) if entry.isreg() else None
            if stream is None:
                raise ValueError("S7 source contains a non-regular Git entry")
            with stream:
                data = stream.read()
            path = root / entry.name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError("S7 source bytes differ from Git")
            result[entry.name] = data
    # Include ignored selected files in the comparison; caches are not source inputs.
    actual = {
        str(p.relative_to(root))
        for prefix in SOURCE_PATHS
        for p in ([root / prefix] if (root / prefix).is_file() else (root / prefix).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    }
    if actual != set(result):
        raise ValueError("S7 source file names differ from Git, including ignored files")
    return revision, result


def snapshot(root: Path, destination: Path) -> dict[str, Any]:
    revision, files = git_source(root)
    for name, data in files.items():
        write_private(destination / name, data)
    hashes = {name: content_digest_bytes(data) for name, data in files.items()}
    return {"schema_version": "pixelgym-s7-source-v1", "revision": revision, "files": hashes}


def content_digest_bytes(data: bytes) -> str:
    from pixelgym.grounding.v5.contracts import sha256_bytes

    return "sha256:" + sha256_bytes(data)


def verify_snapshot(source: Path, binding: dict[str, Any]) -> None:
    if {name: _hash(path) for name, path in _files(source).items()} != binding["files"]:
        raise ValueError("S7 retained source differs from its Git binding")


def runtime_lock() -> dict[str, Any]:
    """Freeze the already installed runtime; qualification consumes this exact constraint."""
    distributions: dict[str, dict[str, str]] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"].lower().replace("_", "-")
        entry = {
            "version": dist.version,
            "record_sha256": content_digest_bytes((dist.read_text("RECORD") or "").encode()),
        }
        if name == "pixelgym":
            # The editable project may have both egg-info and dist-info. Its executable
            # bytes are bound to Git separately, rather than an editable-install RECORD.
            entry = {"version": dist.version, "source_authority": "git-bound-package"}
        # Editable checkouts may occur more than once on sys.path. Collapse only identical
        # metadata; distinct versions or RECORD bytes under one name remain an error.
        if name in distributions and distributions[name] != entry:
            raise ValueError("conflicting installed distribution identity")
        distributions[name] = entry
    return {
        "schema_version": "pixelgym-s7-installed-runtime-lock-v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable_sha256": _hash(Path(sys.executable).resolve()),
        "distributions": distributions,
    }


def verify_runtime(lock: dict[str, Any]) -> None:
    if runtime_lock() != lock:
        raise ValueError("installed runtime differs from the consumed S7 runtime lock")


def worker_binding(
    actions: list[dict[str, int]], source: dict[str, Any], lock: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "pixelgym-s7-worker-binding-v1",
        "factory_module": "pixelgym.grounding.v5.runner",
        "factory_name": "ScriptedStatefulPolicy",
        "factory_kwargs": {"actions": actions},
        "source_sha256": content_digest(source),
        "runtime_lock_sha256": content_digest(lock),
        "provider_endpoint": ENDPOINT,
        "import_authority": ["verified-repository-source", "locked-runtime-site-packages"],
        "protected_authority": "entire-private-evidence-root",
        "require_os_sandbox": True,
        "request_timeout_seconds": 10.0,
    }


def worker_spec(root: Path, evidence_root: Path, binding: dict[str, Any]) -> PolicyWorkerSpec:
    if binding["factory_module"] != "pixelgym.grounding.v5.runner" or (
        binding["factory_name"] != "ScriptedStatefulPolicy"
        or binding["provider_endpoint"] != ENDPOINT
        or binding["require_os_sandbox"] is not True
    ):
        raise ValueError("S7 worker is not the approved scripted factory")
    return PolicyWorkerSpec.build(
        factory_module=binding["factory_module"],
        factory_name=binding["factory_name"],
        factory_kwargs=binding["factory_kwargs"],
        import_roots=(root, Path(sysconfig.get_paths()["purelib"])),
        provider_endpoint=ENDPOINT,
        protected_paths=(evidence_root,),
    )


def verify_worker_binding(
    root: Path, source: dict[str, Any], lock: dict[str, Any], binding: dict[str, Any]
) -> None:
    revision, files = git_source(root)
    actual = {
        "schema_version": "pixelgym-s7-source-v1",
        "revision": revision,
        "files": {name: content_digest_bytes(data) for name, data in files.items()},
    }
    if actual != source or binding != worker_binding(
        binding["factory_kwargs"]["actions"], source, lock
    ):
        raise ValueError("S7 worker factory/source/runtime binding does not verify")
    verify_runtime(lock)


def write_json(path: Path, value: Any) -> None:
    write_private(path, canonical_json_bytes(value))
