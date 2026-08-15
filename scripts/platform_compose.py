"""Run the local platform stack with derived, build-bound source provenance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pixelgym.platform.source_provenance import write_source_provenance

PROVENANCE_RELATIVE_PATH = Path(".cache/platform/source-provenance.json")


def prepare_source_provenance(root: Path) -> Path:
    """Write the file bind-mounted by Compose, refusing Docker's directory trap."""
    provenance_path = root / PROVENANCE_RELATIVE_PATH
    if provenance_path.is_dir():
        raise RuntimeError(
            f"{provenance_path} is a directory, not the required provenance file. "
            "This is commonly left by invoking docker compose directly. Stop the stack with "
            "this wrapper, inspect the directory, then remove it only if it is empty with: "
            f"rmdir {provenance_path}"
        )
    write_source_provenance(root, provenance_path)
    return provenance_path


def _requires_source_provenance(arguments: list[str]) -> bool:
    """Only startup commands create the bind-mounted manifest.

    Keeping ``down`` independent lets an operator stop a stack that was started
    incorrectly and left Docker's directory in place of the expected file.
    """
    return "up" in arguments


def main(argv: list[str] | None = None) -> int:
    root = ROOT
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _requires_source_provenance(arguments):
        try:
            prepare_source_provenance(root)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    command = [
        "docker",
        "compose",
        "--env-file",
        "deploy/.env.example",
        "-f",
        "deploy/compose.yaml",
        *arguments,
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
