"""Opt-in pytest guard for the D5.9 extraction fast-validation command.

Load with ``-p tests.support.d59_offline_guard``. Thread synchronization remains
available; network sockets and wall-clock sleep calls fail before doing work.
"""

from __future__ import annotations

import socket
import time
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def d59_offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("D5.9 offline validation forbids network operations and sleep calls")

    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
