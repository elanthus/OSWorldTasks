"""Run the control-plane migration exactly once before serving."""

from __future__ import annotations

import argparse
import os

from pixelgym.platform.control_store import ControlStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=os.environ.get("PIXELGYM_CONTROL_DB", ".cache/platform/control.db"))
    args = parser.parse_args()
    store = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    store.migrate()
    print(f"control migration complete: {args.database}")


if __name__ == "__main__":
    main()
