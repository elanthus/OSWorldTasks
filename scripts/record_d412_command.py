#!/usr/bin/env python3
"""Record one D4.12 command as redacted raw evidence without judging it."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = "pixelgym-d412-command-record-v1"
_LOCAL_DEMO_SECRETS = (
    "local_demo_postgres_only",
    "local_demo_minio_only",
    "local-demo-csrf-secret-change-before-any-shared-use",
)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _redact(value: str, paths: list[Path]) -> str:
    redacted = value
    replacements = [(str(Path.home()), "<home>")]
    replacements.extend((str(path.resolve()), f"<path-{index}>") for index, path in enumerate(paths))
    for source, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        redacted = redacted.replace(source, replacement)
    for secret in _LOCAL_DEMO_SECRETS:
        redacted = redacted.replace(secret, "<redacted-local-demo-secret>")
    return redacted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--redact-path", action="append", type=Path, default=[])
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    environment = os.environ.copy()
    public_environment: dict[str, str] = {}
    for assignment in args.env:
        name, separator, value = assignment.partition("=")
        if not separator or not name:
            parser.error(f"invalid --env assignment: {assignment!r}")
        environment[name] = value
        public_environment[name] = value
    paths = [args.cwd, *args.redact_path]
    started_at = _timestamp()
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=args.cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    duration = time.monotonic() - started
    record = {
        "schema_version": SCHEMA_VERSION,
        "command": _redact(shlex.join(command), paths),
        "argv": [_redact(argument, paths) for argument in command],
        "cwd": _redact(str(args.cwd.resolve()), paths),
        "environment": public_environment,
        "started_at_utc": started_at,
        "ended_at_utc": _timestamp(),
        "duration_seconds": round(duration, 6),
        "exit_status": completed.returncode,
        "output": _redact(completed.stdout, paths),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
