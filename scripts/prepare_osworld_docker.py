"""Prepare the pinned local Docker host for OSWorld-V2.

This command deliberately does not use OSWorld's built-in downloader because
the pinned release still points that downloader at a mutable Hugging Face
``main`` URL and its Docker provider launches an unpinned ``latest`` tag.
Here both artifacts are resolved from the release manifest, verified, and
recorded before ``DesktopEnv`` is allowed to start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pixelgym.backends.osworld import (
    ARM64_RUNTIME_BASE_DIGEST,
    ARM64_RUNTIME_BASE_REFERENCE,
    ARM64_RUNTIME_IMAGE_REFERENCE,
    ARM64_RUNTIME_SOURCE_COMMIT,
    GUEST_ARTIFACT_NAME,
    GUEST_ARTIFACT_REPOSITORY,
    GUEST_ARTIFACT_SHA256,
    GUEST_ARTIFACT_SIZE,
    GUEST_ARTIFACT_TAG,
    OSWORLD_COMMIT,
    OSWORLD_RELEASE,
    OSWORLD_RELEASE_MANIFEST_URL,
    OSWORLD_REPOSITORY,
    OSWORLD_TAG,
    RUNTIME_IMAGE_DIGEST,
    RUNTIME_IMAGE_REFERENCE,
    RUNTIME_IMAGE_REPOSITORY,
)

DEFAULT_CACHE_DIR = Path(".cache/osworld")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ARM64_RUNTIME_DOCKER_CONTEXT = REPOSITORY_ROOT / "docker" / "osworld-arm64"
GUEST_URL = (
    "https://huggingface.co/datasets/"
    f"{GUEST_ARTIFACT_REPOSITORY}/resolve/{GUEST_ARTIFACT_TAG}/{GUEST_ARTIFACT_NAME}"
)
_COPY_CHUNK = 8 * 1024 * 1024
_PROGRESS_INTERVAL = 256 * 1024 * 1024


class PreparationError(RuntimeError):
    pass


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PreparationError(f"required command is not installed: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or f"exit {exc.returncode}"
        raise PreparationError(f"{' '.join(args)} failed: {detail}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_runtime_image() -> dict[str, Any]:
    _run("docker", "version")
    engine = json.loads(_run("docker", "version", "--format", "{{json .Server}}").stdout)
    engine_info = json.loads(_run("docker", "info", "--format", "{{json .}}").stdout)
    mutable_ref = f"{RUNTIME_IMAGE_REPOSITORY}:latest"

    if platform.system() == "Darwin" and engine.get("Arch") == "arm64":
        if not ARM64_RUNTIME_DOCKER_CONTEXT.is_dir():
            raise PreparationError(
                f"ARM64 runtime Docker context is missing: {ARM64_RUNTIME_DOCKER_CONTEXT}"
            )
        _run(
            "docker",
            "build",
            "--platform",
            "linux/arm64",
            "--provenance=false",
            "--tag",
            ARM64_RUNTIME_IMAGE_REFERENCE,
            str(ARM64_RUNTIME_DOCKER_CONTEXT),
        )
        image_details = json.loads(
            _run(
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .}}",
                ARM64_RUNTIME_IMAGE_REFERENCE,
            ).stdout.strip()
        )
        if image_details.get("Architecture") != "arm64":
            raise PreparationError("local OSWorld compatibility image is not arm64")
        labels = image_details.get("Config", {}).get("Labels") or {}
        if labels.get("io.pixelgym.base.digest") != ARM64_RUNTIME_BASE_DIGEST:
            raise PreparationError("local OSWorld compatibility image has the wrong base digest")
        if labels.get("org.opencontainers.image.revision") != ARM64_RUNTIME_SOURCE_COMMIT:
            raise PreparationError(
                "local OSWorld compatibility image has the wrong source revision"
            )
        if image_details.get("Config", {}).get("Volumes"):
            raise PreparationError(
                "local OSWorld compatibility image must not declare persistent volumes"
            )

        existing = _run(
            "docker", "image", "inspect", "--format", "{{.Id}}", mutable_ref, check=False
        )
        previous_id = existing.stdout.strip() if existing.returncode == 0 else None
        local_id = image_details["Id"]
        _run("docker", "tag", ARM64_RUNTIME_IMAGE_REFERENCE, mutable_ref)
        tagged_id = _run(
            "docker", "image", "inspect", "--format", "{{.Id}}", mutable_ref
        ).stdout.strip()
        if tagged_id != local_id:
            raise PreparationError(
                "runtime compatibility tag did not resolve to the ARM64 image ID"
            )

        return {
            "strategy": "local-arm64-compatibility-build",
            "reference": ARM64_RUNTIME_IMAGE_REFERENCE,
            "base_reference": ARM64_RUNTIME_BASE_REFERENCE,
            "base_digest": ARM64_RUNTIME_BASE_DIGEST,
            "source_commit": ARM64_RUNTIME_SOURCE_COMMIT,
            "local_image_id": local_id,
            "image_architecture": image_details.get("Architecture"),
            "repo_digests": image_details.get("RepoDigests", []),
            "upstream_compatibility_tag": mutable_ref,
            "compatibility_tag_previous_image_id": previous_id,
            "engine_os": engine.get("Os"),
            "engine_arch": engine.get("Arch"),
            "engine_version": engine.get("Version"),
            "engine_cpus": engine_info.get("NCPU"),
            "engine_memory_bytes": engine_info.get("MemTotal"),
        }

    _run("docker", "pull", RUNTIME_IMAGE_REFERENCE)

    pinned_id = _run(
        "docker", "image", "inspect", "--format", "{{.Id}}", RUNTIME_IMAGE_REFERENCE
    ).stdout.strip()
    existing = _run("docker", "image", "inspect", "--format", "{{.Id}}", mutable_ref, check=False)
    if existing.returncode == 0 and existing.stdout.strip() != pinned_id:
        raise PreparationError(
            f"refusing to replace existing {mutable_ref}; it points at a different image"
        )
    _run("docker", "tag", RUNTIME_IMAGE_REFERENCE, mutable_ref)
    tagged_id = _run(
        "docker", "image", "inspect", "--format", "{{.Id}}", mutable_ref
    ).stdout.strip()
    if tagged_id != pinned_id:
        raise PreparationError("runtime latest tag did not resolve to the pinned image ID")

    image_details = json.loads(
        _run(
            "docker", "image", "inspect", "--format", "{{json .}}", RUNTIME_IMAGE_REFERENCE
        ).stdout.strip()
    )
    return {
        "strategy": "release-manifest-image",
        "reference": RUNTIME_IMAGE_REFERENCE,
        "repository_digest": RUNTIME_IMAGE_DIGEST,
        "local_image_id": pinned_id,
        "image_architecture": image_details.get("Architecture"),
        "repo_digests": image_details.get("RepoDigests", []),
        "upstream_compatibility_tag": mutable_ref,
        "engine_os": engine.get("Os"),
        "engine_arch": engine.get("Arch"),
        "engine_version": engine.get("Version"),
        "engine_cpus": engine_info.get("NCPU"),
        "engine_memory_bytes": engine_info.get("MemTotal"),
    }


def download_guest_artifact(cache_dir: Path) -> Path:
    destination = cache_dir / GUEST_ARTIFACT_NAME
    partial = destination.with_suffix(destination.suffix + ".partial")

    if destination.is_file():
        if (
            destination.stat().st_size == GUEST_ARTIFACT_SIZE
            and _sha256(destination) == GUEST_ARTIFACT_SHA256
        ):
            return destination
        raise PreparationError(
            f"existing guest artifact failed release size/checksum validation: {destination}"
        )

    resume_at = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "PixelGym-OSWorld/0.1"}
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
    request = urllib.request.Request(GUEST_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        status = getattr(response, "status", 200)
        if resume_at and status != 206:
            resume_at = 0
            mode = "wb"
        else:
            mode = "ab" if resume_at else "wb"
        downloaded = resume_at
        next_progress = ((downloaded // _PROGRESS_INTERVAL) + 1) * _PROGRESS_INTERVAL
        with partial.open(mode) as output:
            while True:
                chunk = response.read(_COPY_CHUNK)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_progress:
                    print(
                        f"downloaded {downloaded / (1024**3):.1f} / "
                        f"{GUEST_ARTIFACT_SIZE / (1024**3):.1f} GiB",
                        file=sys.stderr,
                        flush=True,
                    )
                    next_progress += _PROGRESS_INTERVAL

    if partial.stat().st_size != GUEST_ARTIFACT_SIZE:
        raise PreparationError(
            f"guest download size is {partial.stat().st_size}, expected {GUEST_ARTIFACT_SIZE}"
        )
    actual = _sha256(partial)
    if actual != GUEST_ARTIFACT_SHA256:
        raise PreparationError(
            f"guest artifact checksum is sha256:{actual}, expected sha256:{GUEST_ARTIFACT_SHA256}"
        )
    partial.replace(destination)
    return destination


def extract_guest_image(archive: Path, cache_dir: Path) -> Path:
    expected_name = GUEST_ARTIFACT_NAME.removesuffix(".zip")
    destination = cache_dir / expected_name
    if destination.is_file():
        return destination
    partial = destination.with_suffix(destination.suffix + ".partial")
    if partial.exists():
        partial.unlink()

    with zipfile.ZipFile(archive) as zf:
        files = [member for member in zf.infolist() if not member.is_dir()]
        matching = [member for member in files if Path(member.filename).name == expected_name]
        if len(matching) != 1:
            raise PreparationError(
                f"guest archive must contain exactly one {expected_name!r}, found "
                f"{[member.filename for member in files]}"
            )
        member = matching[0]
        if Path(member.filename).is_absolute() or ".." in Path(member.filename).parts:
            raise PreparationError(f"unsafe archive member: {member.filename}")
        with zf.open(member) as source, partial.open("wb") as output:
            shutil.copyfileobj(source, output, length=_COPY_CHUNK)
    partial.replace(destination)
    return destination


def prepare(cache_dir: Path) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    runtime = prepare_runtime_image()
    archive = download_guest_artifact(cache_dir)
    image = extract_guest_image(archive, cache_dir)
    metadata = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "upstream_repository": OSWORLD_REPOSITORY,
        "upstream_tag": OSWORLD_TAG,
        "upstream_commit": OSWORLD_COMMIT,
        "release": OSWORLD_RELEASE,
        "release_manifest_url": OSWORLD_RELEASE_MANIFEST_URL,
        "provider": "docker-local",
        "host_system": platform.system(),
        "host_machine": platform.machine(),
        "runtime": runtime,
        "guest_artifact": {
            "repository": GUEST_ARTIFACT_REPOSITORY,
            "tag": GUEST_ARTIFACT_TAG,
            "url": GUEST_URL,
            "archive": str(archive),
            "archive_size": archive.stat().st_size,
            "archive_sha256": f"sha256:{GUEST_ARTIFACT_SHA256}",
            "image": str(image),
            "image_size": image.stat().st_size,
        },
    }
    metadata_path = cache_dir / "preparation.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.cache_dir)
    except PreparationError as exc:
        print(f"OSWorld Docker preparation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
