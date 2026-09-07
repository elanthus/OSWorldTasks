"""Keep test subprocesses bound to the checkout pytest was started from."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import settings

# Registered here, in the repository's top-level `tests/conftest.py`, so it is
# always an *initial* conftest regardless of whether pytest is invoked as
# `pytest`, `pytest tests`, or `pytest tests/unit`: pytest collects conftest
# files along the path from each argument up to rootdir before it processes
# `addopts`, and `--hypothesis-profile=ci` (below) needs the profile to exist
# by then. Registering it in `tests/unit/conftest.py` instead works only when
# an argument resolves under `tests/unit` — anchoring on `tests/` skips it and
# `--hypothesis-profile=ci` raises `InvalidArgument` for an unknown profile.
#
# derandomize=True and a disabled example database make generated cases
# repeatable across local and CI runs; deadline=None removes wall-clock
# flakiness from slower CI hosts.
settings.register_profile(
    "ci",
    settings(max_examples=50, derandomize=True, deadline=None, database=None),
)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Prepend the active repository to imports inherited by child processes.

    A virtual environment may be shared by multiple Git worktrees. Its editable
    install still names the checkout where the environment was created, so a
    subprocess launched from a temporary directory can otherwise import code
    from that checkout instead of the one under test.
    """
    repository_root = Path(session.config.rootpath).resolve()
    inherited = os.environ.get("PYTHONPATH")
    entries = [str(repository_root)]
    if inherited:
        entries.append(inherited)
    os.environ["PYTHONPATH"] = os.pathsep.join(entries)
