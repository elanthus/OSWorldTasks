"""Keep test subprocesses bound to the checkout pytest was started from."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


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
