#!/usr/bin/env python3
"""Record one platform-gate command as redacted raw evidence without judging it."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from pixelgym.evidence_redaction import redact_evidence_text, standard_path_replacements

SCHEMA_VERSION = "pixelgym-d412-command-record-v1"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


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
    path_replacements = standard_path_replacements([args.cwd, *args.redact_path])
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
        "command": redact_evidence_text(shlex.join(command), path_replacements),
        "argv": [redact_evidence_text(argument, path_replacements) for argument in command],
        "cwd": redact_evidence_text(str(args.cwd.resolve()), path_replacements),
        "environment": {
            name: redact_evidence_text(value, path_replacements)
            for name, value in public_environment.items()
        },
        "started_at_utc": started_at,
        "ended_at_utc": _timestamp(),
        "duration_seconds": round(duration, 6),
        "exit_status": completed.returncode,
        "output": redact_evidence_text(completed.stdout, path_replacements),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
