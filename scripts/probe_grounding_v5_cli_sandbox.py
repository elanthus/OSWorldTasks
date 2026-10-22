#!/usr/bin/env python3
"""Run the no-cost CLI sandbox probe and print its JSON result.

Three sandboxed launches of the installed Claude Code CLI with a synthetic placeholder
token: ``auth status``, a ``--print`` through a proxy that refuses every target, and a
``--print`` with no proxy. No request can reach a provider. Exit 0 when every expected
observation holds, 1 otherwise. Design record: docs/v5-policy-sandbox.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pixelgym.grounding.v5.cli_sandbox_probe import run_cli_sandbox_probe


def _placeholder_token() -> str:
    # Credential-shaped so the CLI accepts the route; synthetic, never valid anywhere.
    return "".join(("s", "k", "-ant-oat01-placeholder-", "0" * 24))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cli-executable", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    args = parser.parse_args(argv)
    executable = args.cli_executable
    if executable is None:
        located = shutil.which("claude")
        if located is None:
            print("claude is not installed", file=sys.stderr)
            return 2
        executable = Path(located)
    result = run_cli_sandbox_probe(
        cli_executable=executable.resolve(),
        placeholder_token=_placeholder_token(),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
