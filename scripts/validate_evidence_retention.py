"""Run a credential-free retention campaign or verify/report its stored evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import (
    _checkout,
    _no_links,
    _separate,
    backup_archive,
    check_cleanup,
    create_archive,
    restore_archive,
    source_fingerprint,
    write_private,
)
from pixelgym.grounding.v5.retention_validation import (
    capture_campaign,
    regenerate_report,
    render_report,
    replay_campaign,
    source_snapshot,
    verify_source,
)
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _environment() -> dict[str, str]:
    # No inherited provider credentials, user configuration, Python injection, or Git controls.
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _restored_check(restored: Path, logs: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        (
            "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('scripts.validate_evidence_retention',run_name='__main__')"
        ),
        str(restored / "source"),
        "verify",
        "--run",
        str(restored),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=restored,
            env=_environment(),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        write_private(logs / "stdout.txt", exc.stdout or b"")
        write_private(logs / "stderr.txt", exc.stderr or b"")
        write_private(
            logs / "process.json", canonical_json_bytes({"exit_status": None, "timeout": True})
        )
        raise ValueError("restored-source verification timed out; private logs preserved") from None
    write_private(logs / "stdout.txt", completed.stdout)
    write_private(logs / "stderr.txt", completed.stderr)
    write_private(
        logs / "process.json",
        canonical_json_bytes({"exit_status": completed.returncode, "timeout": False}),
    )
    if completed.returncode != 0:
        # Do not copy potentially private child output into a publishable receipt.
        raise ValueError("restored-source verification failed; private run evidence was preserved")
    result = json.loads(completed.stdout)
    return {
        "command": "python -I -B <retained-source-entry-point> verify --run <restored-run>",
        "exit_status": completed.returncode,
        "runtime_seconds": round(time.monotonic() - started, 6),
        "output": result,
    }


def run_validation(
    store: Path, backup_store: Path, run_id: str, public_output: Path, *, root: Path = ROOT
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", run_id):
        raise ValueError("run ID must be a lowercase name of at most 80 characters")
    store, backup_store, public_output = map(_no_links, (store, backup_store, public_output))
    _separate(store, backup_store, public_output)
    if _checkout(store) is not None or _checkout(backup_store) is not None:
        raise ValueError("retention stores must be outside Git checkouts")
    run, archive, backup = (
        store / "runs" / run_id,
        store / "archives" / run_id,
        backup_store / run_id,
    )
    if any(path.exists() for path in (run, archive, backup, public_output)):
        raise FileExistsError("run, archive, backup, or public output already exists")
    run.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    capture_campaign(run)
    files = source_snapshot(root, run)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        env=_environment(),
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    binding = {
        "git_revision": revision,
        "files": files,
        "python": platform.python_version(),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("numpy", "pillow", "gymnasium")
        },
    }
    write_private(run / "source-binding.json", canonical_json_bytes(binding))
    replay = replay_campaign(run)
    write_private(run / "replay.json", canonical_json_bytes(replay))
    report = regenerate_report(run)
    write_private(run / "report.json", canonical_json_bytes(report))
    capture = create_archive(run, archive)
    digest = capture["manifest_digest"]
    backup_archive(archive, backup, expected_digest=digest)
    checks = []
    with tempfile.TemporaryDirectory(prefix="pixelgym-independent-restore-") as temporary:
        for index, retained in enumerate((archive, backup)):
            restored = Path(temporary).resolve() / str(index)
            restore_archive(retained, restored, expected_digest=digest)
            before = source_fingerprint(restored)
            checks.append(_restored_check(restored, store / "checks" / run_id / str(index)))
            if source_fingerprint(restored) != before:
                raise ValueError("verification mutated restored evidence")
    receipt = check_cleanup(run, archive, backup, expected_digest=digest)
    receipt.update(
        {
            "run_id": run_id,
            "report_digest": content_digest(report),
            "restored_source_checks": checks,
            "real_provider_requests": 0,
            "runtime_seconds": round(time.monotonic() - started, 6),
            "limitations": [
                "two local copies may share a disk; not disaster-recovery proof",
                "cleanup receipt does not intercept external worktree deletion",
                "does not restore missing historical D5.9 journals",
            ],
        }
    )
    # Keep the receipt outside the captured source, so its fingerprint remains valid.
    write_private(store / "receipts" / (run_id + ".json"), canonical_json_bytes(receipt))
    public_output.mkdir(parents=True, mode=0o700)
    for name, value in (
        ("observations.json", json.loads((run / "observations.json").read_bytes())),
        ("report.json", report),
        ("source-binding.json", binding),
        ("archive-manifest.json", json.loads((archive / "manifest.json").read_bytes())),
        ("retention-receipt.json", receipt),
    ):
        write_private(public_output / name, canonical_json_bytes(value))
    write_private(public_output / "REPORT.md", render_report(report).encode())
    return {
        "run_id": run_id,
        "manifest_digest": digest,
        "report_digest": content_digest(report),
        "case_count": report["case_count"],
        "real_provider_requests": 0,
        "restored_source_checks": checks,
        "runtime_seconds": receipt["runtime_seconds"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--store", type=Path, required=True)
    execute.add_argument("--backup-store", type=Path, required=True)
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--public-output", type=Path, required=True)
    for name in ("verify", "report"):
        child = commands.add_parser(name)
        child.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = run_validation(args.store, args.backup_store, args.run_id, args.public_output)
    elif args.command == "verify":
        verify_source(args.run)
        replay = replay_campaign(args.run)
        if replay != json.loads((args.run / "replay.json").read_bytes()):
            raise ValueError("restored replay differs from stored observations")
        report = regenerate_report(args.run)
        if report != json.loads((args.run / "report.json").read_bytes()):
            raise ValueError("regenerated report differs from stored report")
        result = {
            "replay_digest": content_digest(replay),
            "report_digest": content_digest(report),
            "case_count": report["case_count"],
            "real_provider_requests": 0,
        }
    else:
        result = regenerate_report(args.run)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
