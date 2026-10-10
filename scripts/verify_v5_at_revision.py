"""Verify eight frozen v5 packages with their historical source, without provider calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pixelgym.evidence_redaction import redact_evidence_text, standard_path_replacements

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_REVISION = "b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61"
SCHEMA = "pixelgym-v5-historical-verification-v1"


@dataclass(frozen=True)
class Case:
    revision: str
    module: str
    binding_path: str
    binding_keys: tuple[str, ...]
    source_argument: bool = False


CASES = {
    "27": Case(
        "09c5b4a19794842c1cf0c0a8a74b1cc7c8f59fb5",
        "scripts.prepare_grounding_v5_memory",
        "grounding-v5-d58-design/memory-repair/sources.json",
        (),
    ),
    "42": Case(
        "9446b445d392ed29e7643ec6195d0143ffc70330",
        "scripts.prepare_grounding_v5_review_corrections",
        "grounding-v5-d58-review-corrections/correction.json",
        ("source_file_digests",),
    ),
    "43": Case(
        "1e5d9c0d19acf51505919deefe0d155c2ab22b26",
        "scripts.prepare_grounding_v5_power_report",
        "grounding-v5-d58-review-corrections-v2/power-report-binding.json",
        ("source_file_digests",),
    ),
    "44": Case(
        "dec1fa9d0f060ebe789919c50a7c2335e00f0347",
        "scripts.prepare_grounding_v5_d58_haiku_successor",
        "grounding-v5-d58-haiku-successor/analysis.json",
        ("source_file_digests",),
    ),
    "46": Case(
        "f40157d7f2fc70b5b3d3b659f5cd3168be8e19b3",
        "scripts.prepare_grounding_v5_d59_freeze",
        "grounding-v5-d59-freeze/task-manifest.json",
        ("source_binding", "files"),
        True,
    ),
    "48": Case(
        "ca1c98333cffb5f04beef1d4f56ed09f9840f538",
        "scripts.prepare_grounding_v5_d59_haiku_freeze",
        "grounding-v5-d59-haiku-freeze/execution-plan.json",
        ("source_binding", "files"),
        True,
    ),
    "49": Case(
        "1201df8a773add79733d392f876b579752f797c5",
        "scripts.prepare_grounding_v5_d59_haiku_retry_successor",
        "grounding-v5-d59-haiku-api-retry-successor/execution-plan.json",
        ("source_binding", "files"),
        True,
    ),
    "50": Case(
        "b3167ff49576aec4001444ba3166d37cfc8dfda4",
        "scripts.prepare_grounding_v5_d59_haiku_network_retry",
        "grounding-v5-d59-haiku-network-retry/execution-plan.json",
        ("source_binding", "files"),
        True,
    ),
}


def _git(root: Path, *args: str) -> bytes:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        ["git", *args], cwd=root, env=environment, capture_output=True, check=False
    )
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout


def _extract(root: Path, revision: str, paths: tuple[str, ...], target: Path) -> None:
    payload = _git(root, "archive", revision, *paths)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        # Historical inputs are regular files, not links into the caller's checkout.
        if any(not (member.isfile() or member.isdir()) for member in archive.getmembers()):
            raise ValueError("historical archive contains a non-regular entry")
        archive.extractall(target, filter="data")


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory(tree: Path) -> dict[str, str]:
    result = {}
    for path in sorted((tree / "artifacts").rglob("*")):
        if path.is_symlink():
            raise ValueError("artifact inventory contains a symlink")
        if path.is_file():
            result[path.relative_to(tree).as_posix()] = _hash(path)
    return result


def _bindings(tree: Path, case: Case) -> dict[str, str]:
    value = json.loads((tree / "artifacts" / case.binding_path).read_text())
    for key in case.binding_keys:
        value = value[key]
    if not isinstance(value, dict) or not value:
        raise ValueError("source binding must be a nonempty object")
    for name, digest in value.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"invalid bound path: {name}")
        if _hash(tree / path) != digest:
            raise ValueError(f"historical source binding mismatch: {name}")
    return value


def verify(record: str, *, root: Path = ROOT, timeout: float = 1800) -> dict[str, Any]:
    """Run a fixed read-only command; return raw results even on setup or command failure."""
    if record not in CASES:
        raise ValueError(f"unsupported original record: {record}")
    if timeout <= 0 or not timeout < float("inf"):
        raise ValueError("timeout must be positive and finite")
    case = CASES[record]
    started = time.monotonic()
    result: dict[str, Any] = {
        "original_record": record,
        "source_revision": case.revision,
        "artifact_revision": EVIDENCE_REVISION,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "exit_status": None,
        "output": "",
        "verification_errors": [],
    }
    with tempfile.TemporaryDirectory(prefix="v5-historical-") as directory:
        tree = Path(directory)
        replacements = standard_path_replacements([root, tree, Path(sys.executable).parent.parent])
        try:
            _extract(
                root, case.revision, ("pixelgym", "scripts", "pyproject.toml", "requirements"), tree
            )
            _extract(root, EVIDENCE_REVISION, ("artifacts",), tree)
            result["source_file_hashes"] = _bindings(tree, case)
            before = _inventory(tree)
            result["artifact_file_count"] = len(before)
            result["artifact_inventory_sha256"] = hashlib.sha256(
                json.dumps(before, sort_keys=True).encode()
            ).hexdigest()
            # Historical retry checks strip GIT_* variables. Give their read-only Git
            # object queries a repository identity without registering a new worktree.
            gitdir = _git(root, "rev-parse", "--absolute-git-dir").decode().strip()
            (tree / ".git").write_text(f"gitdir: {gitdir}\n")
            (tree / "home").mkdir()
            (tree / "tmp").mkdir()
            environment = {
                "PATH": os.defpath,
                "HOME": str(tree / "home"),
                "TMPDIR": str(tree / "tmp"),
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONUTF8": "1",
                "PYTHONPATH": str(tree),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            command = [sys.executable, "-m", case.module, "--verify"]
            if case.source_argument:
                command += ["--source-revision", case.revision]
            result.update(
                command=shlex.join(command),
                cwd=str(tree),
                environment_variable_names=sorted(environment),
                timeout_seconds=timeout,
            )
            command_started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=tree,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
                result.update(exit_status=completed.returncode, output=completed.stdout)
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                result.update(
                    output=output.decode(errors="replace") if isinstance(output, bytes) else output,
                    timed_out=True,
                )
                result["verification_errors"].append("historical verifier exceeded timeout")
            result["command_duration_seconds"] = round(time.monotonic() - command_started, 6)
            after = _inventory(tree)
            changes = sorted(
                path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
            )
            result["artifact_changes"] = changes
            if changes:
                result["verification_errors"].append("historical verifier mutated artifact files")
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
            result["verification_errors"].append(str(error))
        result["duration_seconds"] = round(time.monotonic() - started, 6)
        result["ended_at_utc"] = datetime.now(UTC).isoformat()
        return json.loads(redact_evidence_text(json.dumps(result), replacements))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", choices=[*CASES, "all"])
    parser.add_argument("--output", type=Path, help="create a new JSON record; never overwrite")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    args = parser.parse_args()
    if "all" in args.records and args.records != ["all"]:
        parser.error("all cannot be combined with record numbers")
    if len(set(args.records)) != len(args.records):
        parser.error("duplicate record numbers")
    if not 0 < args.timeout_seconds < float("inf"):
        parser.error("timeout must be positive and finite")
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new path")
    records = list(CASES) if args.records == ["all"] else args.records
    observations = [verify(record, timeout=args.timeout_seconds) for record in records]
    report = {
        "schema_version": SCHEMA,
        "verdict": None,
        "launcher_sha256": _hash(Path(__file__)),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "dependency_versions": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "Pillow", "gymnasium", "pydantic", "jsonschema")
        },
        "observations": observations,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    print(payload, end="")
    raise SystemExit(
        int(any(row["exit_status"] != 0 or row["verification_errors"] for row in observations))
    )


if __name__ == "__main__":
    main()
