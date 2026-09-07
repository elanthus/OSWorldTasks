#!/usr/bin/env python3
"""Compare grounding maintenance surfaces and built-wheel contents at two revisions."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SOURCE_SUFFIXES = frozenset({".py", ".json", ".js", ".css", ".html"})


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-revision", required=True)
    parser.add_argument("--after-revision", default="HEAD")
    return parser.parse_args(argv)


def _run(root: Path, *command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd or root,
        check=True,
        capture_output=True,
    )


def _resolve(root: Path, revision: str) -> str:
    return _run(root, "git", "rev-parse", f"{revision}^{{commit}}").stdout.decode().strip()


def _snapshot(root: Path, revision: str, destination: Path) -> None:
    archive = _run(root, "git", "archive", "--format=tar", revision).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def _build_wheel(snapshot: Path, wheel_directory: Path) -> Path:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(snapshot),
            "--no-deps",
            "--no-build-isolation",
            "--disable-pip-version-check",
            "-w",
            str(wheel_directory),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    wheels = list(wheel_directory.glob("pixelgym-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one PixelGym wheel, found {len(wheels)}")
    return wheels[0]


def _line_count(root: Path, relative_paths: Sequence[Path]) -> int:
    return sum(len((root / path).read_bytes().splitlines()) for path in relative_paths)


def _measure(root: Path, revision: str, temporary_root: Path, label: str) -> dict[str, Any]:
    resolved = _resolve(root, revision)
    snapshot = temporary_root / f"{label}-source"
    wheel_directory = temporary_root / f"{label}-wheel"
    snapshot.mkdir()
    wheel_directory.mkdir()
    _snapshot(root, resolved, snapshot)
    files = sorted(path.relative_to(snapshot) for path in snapshot.rglob("*") if path.is_file())
    production = [
        path
        for path in files
        if path.parts[:2] == ("pixelgym", "grounding") and path.suffix in SOURCE_SUFFIXES
    ]
    scripts = [path for path in files if path.parent == Path("scripts") and path.suffix == ".py"]
    maintenance = [
        path.as_posix()
        for path in files
        if (
            path.parts[:2] == ("pixelgym", "grounding")
            and (
                "d56_" in path.name
                or path.name.startswith("calibration_v3")
                or path.name.startswith("calibration_v4")
            )
        )
        or (
            path.parent == Path("scripts")
            and (
                path.name.startswith("run_grounding_v4")
                or path.name.startswith("run_grounding_v5_d56")
            )
        )
    ]
    wheel = _build_wheel(snapshot, wheel_directory)
    with zipfile.ZipFile(wheel) as archive:
        wheel_files = sorted(name for name in archive.namelist() if not name.endswith("/"))
    grounding_wheel_files = [
        name for name in wheel_files if name.startswith("pixelgym/grounding/")
    ]
    return {
        "revision": resolved,
        "grounding_production_physical_loc": _line_count(snapshot, production),
        "top_level_python_script_count": len(scripts),
        "experiment_specific_maintenance_surface_count": len(maintenance),
        "experiment_specific_maintenance_surface_files": maintenance,
        "wheel_file_count": len(wheel_files),
        "grounding_wheel_file_count": len(grounding_wheel_files),
        "grounding_wheel_files": grounding_wheel_files,
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pixelgym-maintenance-measurement-") as temporary:
        temporary_root = Path(temporary)
        before = _measure(root, args.before_revision, temporary_root, "before")
        after = _measure(root, args.after_revision, temporary_root, "after")
    report = {
        "schema_version": "pixelgym-grounding-maintenance-measurement-v1",
        "reproduction_command": (
            ".venv/bin/python scripts/measure_grounding_maintenance.py "
            f"--before-revision {before['revision']} --after-revision {after['revision']}"
        ),
        "measurement_rules": {
            "grounding_production_physical_loc": (
                "Physical lines in .py/.json/.js/.css/.html files under pixelgym/grounding; "
                "tests and any historical legacy/ tree (at a queried before/after "
                "revision) are outside that tree."
            ),
            "top_level_python_script_count": "Tracked .py files directly under scripts/.",
            "package_contents": "File names read from wheels built offline with pip --no-build-isolation.",
            "maintenance_surface": (
                "Shipped d56_/calibration_v3*/calibration_v4* grounding modules plus top-level "
                "run_grounding_v4*/run_grounding_v5_d56* wrappers."
            ),
        },
        "before": before,
        "after": after,
        "delta": {
            "grounding_production_physical_loc": (
                after["grounding_production_physical_loc"]
                - before["grounding_production_physical_loc"]
            ),
            "top_level_python_script_count": (
                after["top_level_python_script_count"]
                - before["top_level_python_script_count"]
            ),
            "experiment_specific_maintenance_surface_count": (
                after["experiment_specific_maintenance_surface_count"]
                - before["experiment_specific_maintenance_surface_count"]
            ),
            "grounding_wheel_file_count": (
                after["grounding_wheel_file_count"] - before["grounding_wheel_file_count"]
            ),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
