"""Build or verify the response-free D5.9 freeze artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5.d59_freeze import (
    admission_evidence,
    execution_plan,
    task_manifest,
)
from pixelgym.serialization import canonical_json_bytes

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "artifacts/grounding-v5-d59-freeze"


def expected(*, source_revision: str, include_admission: bool) -> dict[str, bytes]:
    price = json.loads((PUBLIC / "price-recheck.json").read_text(encoding="utf-8"))
    manifest = task_manifest(ROOT, source_revision=source_revision)
    admission = (
        admission_evidence()
        if include_admission
        else json.loads((PUBLIC / "admission.json").read_text(encoding="utf-8"))
    )
    values = {
        "task-manifest.json": manifest,
        "admission.json": admission,
        "execution-plan.json": execution_plan(
            ROOT,
            source_revision=source_revision,
            price_snapshot=price,
            task_manifest_value=manifest,
            admission_value=admission,
        ),
    }
    outputs = {name: canonical_json_bytes(value) + b"\n" for name, value in values.items()}
    if not include_admission:
        outputs.pop("admission.json")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--skip-admission", action="store_true")
    args = parser.parse_args()
    outputs = expected(
        source_revision=args.source_revision,
        include_admission=not args.skip_admission,
    )
    for name, payload in outputs.items():
        path = PUBLIC / name
        if args.verify:
            if path.read_bytes() != payload:
                raise SystemExit(f"stored D5.9 artifact differs: {path}")
        else:
            if path.exists():
                raise SystemExit(f"refusing to overwrite D5.9 artifact: {path}")
            path.write_bytes(payload)
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
