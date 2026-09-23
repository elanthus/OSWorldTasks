"""Build or verify the response-free D5.9 Haiku freeze successor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.d59_haiku_freeze import expected_outputs

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "artifacts/grounding-v5-d59-haiku-freeze"


def write_outputs(
    outputs: dict[str, bytes], *, verify: bool, public: Path = PUBLIC
) -> None:
    paths = {name: public / name for name in outputs}
    if not verify:
        existing = [path for path in paths.values() if path.exists()]
        if existing:
            raise SystemExit(f"refusing to overwrite D5.9 Haiku artifacts: {existing}")
        public.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        path = paths[name]
        if verify:
            if path.read_bytes() != payload:
                raise SystemExit(f"stored D5.9 Haiku artifact differs: {path}")
        else:
            path.write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    outputs = expected_outputs(ROOT, source_revision=args.source_revision)
    write_outputs(outputs, verify=args.verify)
    print(
        json.dumps(
            {
                "verified": args.verify,
                "files": sorted(outputs),
                "provider_calls_made": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
