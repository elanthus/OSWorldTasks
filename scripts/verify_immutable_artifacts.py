"""Verify pinned candidate artifacts without parsing, scoring, or calling a provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.immutable_store import (
    ImmutableStoreError,
    LocalImmutableStore,
    S3ImmutableStore,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--local-root")
    parser.add_argument("--output")
    args = parser.parse_args()
    control = ControlStore(args.database)
    if args.local_root:
        store = LocalImmutableStore(Path(args.local_root))
    else:
        bucket = os.environ.get("PIXELGYM_IMMUTABLE_BUCKET")
        if not bucket:
            raise SystemExit("provide --local-root or PIXELGYM_IMMUTABLE_BUCKET")
        store = S3ImmutableStore(
            bucket=bucket,
            prefix=os.environ.get("PIXELGYM_IMMUTABLE_PREFIX", "platform"),
            object_lock=os.environ.get("PIXELGYM_OBJECT_LOCK", "true").lower() == "true",
        )
    checked: dict[tuple[str, str], dict] = {}
    failures: list[dict[str, str]] = []
    for candidate in control.list_candidates():
        for reference in candidate.artifacts:
            key = (reference.logical_key, reference.version_id)
            if key in checked:
                continue
            try:
                store.get_verified(reference)
            except ImmutableStoreError as exc:
                failures.append({"logical_key": reference.logical_key, "error": type(exc).__name__})
            else:
                checked[key] = reference.to_dict()
    report = {
        "schema_version": "pixelgym-immutable-verification-v1",
        "verified_count": len(checked),
        "failure_count": len(failures),
        "failures": failures,
        "verified": list(checked.values()),
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(encoded)
    print(encoded, end="")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
