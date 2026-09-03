"""Run the control-plane migration exactly once before serving."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pixelgym.platform.control_store import DEFAULT_BUSY_TIMEOUT_MS, ControlStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=os.environ.get("PIXELGYM_CONTROL_DB", ".cache/platform/control.db"))
    args = parser.parse_args()
    if args.database != ":memory:" and not args.database.startswith("file:"):
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    store = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
        busy_timeout_ms=int(
            os.environ.get("PIXELGYM_SQLITE_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS)
        ),
    )
    store.migrate()
    print(f"control migration complete: {args.database}")


if __name__ == "__main__":
    main()
