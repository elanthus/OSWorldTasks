"""Prepare, qualify, verify, and report S7. Activation and publication are unavailable."""

from __future__ import annotations

import argparse
import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.platform.s7_evidence import (
    build_packages,
    inspect_qualification,
    render_report,
    store_artifacts,
    stored_report,
    verify_packages,
    verify_seal,
)
from pixelgym.platform.s7_qualification import prepare, qualify, read_json, seal


@contextmanager
def no_network() -> Iterator[None]:
    original = socket.socket

    def denied(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("S7 qualification/report/replay cannot open network sockets")

    socket.socket = denied  # type: ignore[misc,assignment]
    try:
        yield
    finally:
        socket.socket = original


def verify(run: Path) -> dict[str, Any]:
    verify_seal(run)
    observed = inspect_qualification(run)
    if observed != read_json(run / "verification.json"):
        raise ValueError("S7 stored semantic verification differs from restored evidence")
    verify_packages(run)
    return {
        "verification_sha256": content_digest(observed),
        "report_sha256": content_digest(stored_report(run)),
        "fake_sends": read_json(run / "results.json")["fake_sends"],
        "real_provider_requests": 0,
        "activation_performed": False,
        "publication_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--run", type=Path, required=True)
    preparation.add_argument("--run-id", required=True)
    preparation.add_argument("--approval", required=True)
    preparation.add_argument("--predecessor-archive", type=Path, action="append", default=[])
    preparation.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    execution = commands.add_parser("qualify")
    execution.add_argument("--run", type=Path, required=True)
    execution.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    for name in ("verify", "report"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    with no_network():
        if args.command == "prepare":
            plan = prepare(
                args.source.resolve(),
                args.run.resolve(),
                run_id=args.run_id,
                approval=args.approval,
                predecessor_archives=tuple(path.resolve() for path in args.predecessor_archive),
            )
            output = {"plan_sha256": content_digest(plan), "fake_sends": 0}
        elif args.command == "qualify":
            qualify(args.source.resolve(), args.run.resolve())
            verification = inspect_qualification(args.run)
            packages = build_packages(args.run, verification)
            store_artifacts(args.run)
            seal(args.run)
            verify_packages(args.run)
            output = {"packages": packages, **verify(args.run)}
        elif args.command == "verify":
            output = verify(args.run)
        else:
            report = stored_report(args.run)
            print(render_report(report), end="")
            return
    print(json.dumps(output, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
