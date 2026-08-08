#!/usr/bin/env python3
"""Run a few steps of PixelGuiEnv against the fake backend and print the reward trace.

Placeholder for D1.2: the fake backend (D1.6) and environment (D1.5) do not exist
yet, so this currently only reports that fact instead of importing them.
"""

import sys


def main() -> int:
    try:
        from pixelgym.backends.fake import FakeBackend  # noqa: F401
        from pixelgym.env import PixelGuiEnv  # noqa: F401
    except ImportError:
        print(
            "Fake backend and environment are not implemented yet "
            "(see plans/day-1-environment-core.md, tasks D1.5 and D1.6)."
        )
        return 1

    raise NotImplementedError("Wire up the demo once PixelGuiEnv and FakeBackend exist.")


if __name__ == "__main__":
    sys.exit(main())
