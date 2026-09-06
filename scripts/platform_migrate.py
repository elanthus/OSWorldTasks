"""Run the control-plane migration exactly once before serving."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pixelgym.platform.control_store import ControlStore, configured_busy_timeout_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=os.environ.get("PIXELGYM_CONTROL_DB", ".cache/platform/control.db"))
    args = parser.parse_args()
    if args.database != ":memory:" and not args.database.startswith("file:"):
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    store = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
        busy_timeout_ms=configured_busy_timeout_ms(),
    )
    store.migrate()
    print(f"control migration complete: {args.database}")


if __name__ == "__main__":
    main()
