"""Prepare, run, verify, and report the S8 scripted serving rehearsal.

Every control-plane transition the ``run`` command performs is bound to the owner-supplied
approval document that ``prepare`` verified; the driver fabricates no approval or demo terms.
Fake sends only: the campaign ledger backed up from the sealed S7 qualification reserves each
send durably before the scripted transport runs. No publication command exists.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.platform.s7_evidence import verify_seal
from pixelgym.platform.s7_qualification import read_json
from pixelgym.platform.s7_source import write_json
from pixelgym.platform.s8_rehearsal import (
    CHECKS_SCHEMA,
    FAILURE_SCHEMA,
    AsgiCaller,
    LoopbackCaller,
    Rehearsal,
    _ledger_state,
    inspect_rehearsal,
    prepare,
    render_report,
    seal,
    stored_report,
)
from scripts.s7_serving import no_network

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def no_inet_sockets() -> Iterator[None]:
    """Deny every internet-family socket; the in-process ASGI caller needs only AF_UNIX pairs."""
    original = socket.socket

    class Guarded(original):  # type: ignore[valid-type,misc]
        def __init__(self, family: int = -1, *args: Any, **kwargs: Any) -> None:
            if family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("S8 rehearsal cannot open internet sockets")
            super().__init__(family, *args, **kwargs)

    socket.socket = Guarded  # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = original  # type: ignore[misc]


def verify(state: Path) -> dict[str, Any]:
    """Recompute the semantic verification from stored bytes; the directory is unchanged."""
    verify_seal(state)
    observed = inspect_rehearsal(state)
    if observed != read_json(state / "verification.json"):
        raise ValueError("S8 stored semantic verification differs from restored evidence")
    plan = read_json(state / "plan.json")
    qualification = Path(plan["qualification_path"])
    qualification_seal_verified: bool | None = None
    if qualification.is_dir():
        verify_seal(qualification)
        qualification_seal_verified = (
            content_digest(read_json(qualification / "seal.json"))
            == plan["qualification_seal_sha256"]
        )
    return {
        "verification_sha256": content_digest(observed),
        "lifecycle_sha256": observed["lifecycle_sha256"],
        "report_sha256": content_digest(stored_report(state)),
        "fake_sends": observed["fake_sends"],
        "campaign_total": observed["campaign_total"],
        "qualification_seal_verified": qualification_seal_verified,
        "real_provider_requests": 0,
        "publication_performed": False,
        "milestone_verdict": None,
    }


def acceptance_checks(root: Path, state: Path) -> dict[str, Any]:
    """Run the documented acceptance commands and retain raw command, status, and output."""
    checks = []
    commands = (
        (
            "stored_verification_replay",
            [sys.executable, "-m", "scripts.s8_rehearsal", "verify", "--state", str(state)],
        ),
    )
    for name, command in commands:
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONPATH": str(root)},
        )
        checks.append(
            {
                "name": name,
                "command": command,
                "exit_status": completed.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    return {"schema_version": CHECKS_SCHEMA, "checks": checks}


def finalize(root: Path, state: Path) -> dict[str, Any]:
    """Store the semantic verification, seal, run acceptance commands, and reseal."""
    try:
        verification = inspect_rehearsal(state)
        write_json(state / "verification.json", verification)
    except BaseException as exc:
        lifecycle = read_json(state / "lifecycle.json")
        counts, _ = _ledger_state(state / "send-budget.sqlite")
        write_json(
            state / "failure.json",
            {
                "schema_version": FAILURE_SCHEMA,
                "lifecycle_complete": True,
                "stage": "verification",
                "cause": type(exc).__name__,
                "message": None,
                "completed_steps": lifecycle["steps"],
                "stopped_step": None,
                "fake_sends": counts,
                "version_attempts": lifecycle["version_attempts"],
                "activation_performed": lifecycle["activation_performed"],
                "publication_performed": False,
                "real_provider_requests": 0,
                "monetary_spend_usd": 0,
                "milestone_verdict": None,
            },
        )
        seal(state)
        raise
    seal(state)
    checks = acceptance_checks(root, state)
    write_json(state / "acceptance-checks.json", checks)
    seal(state)
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--qualification", type=Path, required=True)
    preparation.add_argument("--state", type=Path, required=True)
    preparation.add_argument("--approval", type=Path, required=True)
    preparation.add_argument("--source", type=Path, default=REPOSITORY_ROOT)
    execution = commands.add_parser("run")
    execution.add_argument("--state", type=Path, required=True)
    execution.add_argument("--source", type=Path, default=REPOSITORY_ROOT)
    execution.add_argument(
        "--listener-port",
        type=int,
        default=None,
        help="serve /api/v2 on a real 127.0.0.1 listener instead of the in-process ASGI caller",
    )
    for name in ("verify", "report"):
        command = commands.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with no_network():
            plan = prepare(
                args.source.resolve(),
                args.qualification.resolve(),
                args.state.resolve(),
                approval_path=args.approval.resolve(),
            )
        output: dict[str, Any] = {"plan_sha256": content_digest(plan), "fake_sends": 0}
    elif args.command == "run":
        state, root = args.state.resolve(), args.source.resolve()
        if args.listener_port is None:
            rehearsal = Rehearsal(root, state, caller_factory=AsgiCaller)
            with no_inet_sockets():
                lifecycle = rehearsal.run()
        else:
            port = args.listener_port
            rehearsal = Rehearsal(
                root, state, caller_factory=lambda app: LoopbackCaller(app, port=port)
            )
            lifecycle = rehearsal.run()
        with no_network():
            checks = finalize(root, state)
        output = {
            "lifecycle_sha256": content_digest(lifecycle),
            "fake_sends": lifecycle["fake_sends"],
            "acceptance_exit_statuses": {c["name"]: c["exit_status"] for c in checks["checks"]},
            **verify(state),
        }
    elif args.command == "verify":
        with no_network():
            output = verify(args.state.resolve())
    else:
        with no_network():
            print(render_report(stored_report(args.state.resolve())), end="")
        return
    print(json.dumps(output, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
