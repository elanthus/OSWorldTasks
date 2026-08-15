"""Run the local platform stack with derived, build-bound source provenance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pixelgym.platform.source_provenance import write_source_provenance


def main() -> None:
    root = ROOT
    provenance_path = root / ".cache/platform/source-provenance.json"
    write_source_provenance(root, provenance_path)
    command = [
        "docker",
        "compose",
        "--env-file",
        "deploy/.env.example",
        "-f",
        "deploy/compose.yaml",
        *sys.argv[1:],
    ]
    raise SystemExit(subprocess.run(command, cwd=root, check=False).returncode)


if __name__ == "__main__":
    main()
