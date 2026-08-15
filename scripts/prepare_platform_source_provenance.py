"""Generate Git/build provenance consumed by the local platform Compose stack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pixelgym.platform.source_provenance import write_source_provenance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    provenance = write_source_provenance(ROOT, args.output)
    print(f"generated {provenance.state} source provenance for {provenance.revision}")


if __name__ == "__main__":
    main()
