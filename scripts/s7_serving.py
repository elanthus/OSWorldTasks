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
    _verify_packages,
    build_packages,
    inspect_qualification,
    render_report,
    store_artifacts,
    stored_report,
    verify_packages,
    verify_seal,
)
from pixelgym.platform.s7_qualification import prepare, qualify, read_json, record_failure, seal


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


def finalize(run: Path) -> dict[str, Any]:
    """Seal complete artifacts or retain a sealed, non-approvable post-send failure."""
    try:
        verification = inspect_qualification(run)
        packages = build_packages(run, verification)
        store_artifacts(run)
        _verify_packages(run)
    except BaseException as exc:
        record_failure(run, stage="finalization", cause=exc)
        raise
    seal(run)
    return packages


def predecessor_archive(value: str) -> tuple[Path, str]:
    path, separator, digest = value.rpartition("=sha256:")
    import re

    if not separator or not path or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise argparse.ArgumentTypeError("expected PATH=sha256:<approved manifest digest>")
    return Path(path).resolve(), "sha256:" + digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--run", type=Path, required=True)
    preparation.add_argument("--run-id", required=True)
    preparation.add_argument("--approval", required=True)
    preparation.add_argument(
        "--predecessor-archive", type=predecessor_archive, action="append", default=[]
    )
    predecessors = preparation.add_mutually_exclusive_group(required=True)
    predecessors.add_argument("--expected-predecessor-sends", type=int)
    predecessors.add_argument("--no-predecessors", action="store_true")
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
                predecessor_archives=tuple(args.predecessor_archive),
                expected_predecessor_sends=0
                if args.no_predecessors
                else args.expected_predecessor_sends,
            )
            output = {"plan_sha256": content_digest(plan), "fake_sends": 0}
        elif args.command == "qualify":
            qualify(args.source.resolve(), args.run.resolve())
            packages = finalize(args.run)
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
