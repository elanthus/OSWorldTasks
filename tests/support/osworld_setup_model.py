"""Test-only model of OSWorld guest setup, including the inputs the task page acknowledges.

`OSWorldVendorFormTask.setup` runs fixed guest programs through OSWorld's setup controller. Some of
them drive the real X pointer with pyautogui. In Chromium, a pyautogui click on the page is a
trusted primary mouseup, and the page acknowledges it to its server (reproduced with
`tests/support/browser_backend.py`: one `page.mouse.click` after reset makes `GET /api/input-ack`
return 1). Unit fakes that ignored those programs could not notice that the old pointer-park
program clicked, so a reset that requires exactly `SETUP_ACKNOWLEDGED_INPUTS` acknowledgements
looked fine in tests and broke against a real page.

`SetupModel` records every setup command, answers each probe the way a healthy guest does, and
counts the acknowledgements a page would post for the pyautogui input calls in the programs it
executes. It models pyautogui's pointer position so the park program's report is derived from the
program it ran, not hard-coded.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path
from typing import Any

_ACKNOWLEDGED_CALLS = {
    # One trusted primary mouseup per click; the page acknowledges each one.
    "click": 1,
    "doubleClick": 2,
    "tripleClick": 3,
    "mouseUp": 1,
    # One non-modifier keyup per key.
    "press": 1,
    "keyUp": 1,
}
_UNMODELLED_CALLS = ("typewrite", "write", "hotkey", "dragTo", "drag", "mouseDown", "keyDown")
_CALL = re.compile(r"pyautogui\.([A-Za-z]+)\(([^)]*)\)")


class FakeBaseTask(dict):
    """Stand-in for ``desktop_env.task_base.BaseTask`` (a dict with attribute access)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value


def install_fake_osworld(monkeypatch: Any) -> None:
    desktop_env = types.ModuleType("desktop_env")
    task_base = types.ModuleType("desktop_env.task_base")
    task_base.BaseTask = FakeBaseTask  # type: ignore[attr-defined]
    desktop_env.task_base = task_base  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "desktop_env", desktop_env)
    monkeypatch.setitem(sys.modules, "desktop_env.task_base", task_base)


class SetupModel:
    """A healthy guest's answers to setup, plus the page's acknowledgement count."""

    def __init__(self, cache_dir: Path, record: dict[str, Any], *, screen=(1024, 768)) -> None:
        self.cache_dir = str(cache_dir)
        self.screen_width, self.screen_height = screen
        self.record = record
        self.page_loaded = False
        self.page_acknowledged = 0
        self.pointer = (screen[0] // 2, screen[1] // 2)
        self.commands: list[tuple[list[str], dict[str, Any]]] = []
        self.launches: list[list[str]] = []
        self.downloads: list[dict[str, str]] = []

    def download(self, files: list[dict[str, str]]) -> None:
        self.downloads.extend(files)

    def launch(self, command: list[str]) -> None:
        self.launches.append(command)
        if any("--app=" in part for part in command):
            # A fresh page has acknowledged nothing.
            self.page_loaded = True
            self.page_acknowledged = 0

    def execute(self, command: list[str], *, stdout: str = "", stderr: str = "", **kwargs: Any):
        self.commands.append((command, {"stdout": stdout, "stderr": stderr, **kwargs}))
        program = command[-1] if command else ""
        output = self._run_pyautogui(program) if "pyautogui" in program else self._probe(stdout)
        if stdout:
            (Path(self.cache_dir) / stdout).write_text(output, encoding="utf-8")
        if stderr:
            (Path(self.cache_dir) / stderr).write_text("", encoding="utf-8")

    def _probe(self, stdout: str) -> str:
        if "page-ready" in stdout:
            return json.dumps({"ready": True})
        if "display-size" in stdout:
            return "DISPLAY_SIZE_READY\n"
        if "desktop-ready" in stdout:
            return "DESKTOP_READY\n"
        if "reset" in stdout:
            return json.dumps(
                {
                    "task_id": self.record["task_id"],
                    "seed": self.record["seed"],
                    "requires_reload": True,
                }
            )
        if "ready" in stdout:
            return "READY\n"
        return ""

    def _run_pyautogui(self, program: str) -> str:
        for name in _UNMODELLED_CALLS:
            if f"pyautogui.{name}(" in program:
                raise AssertionError(f"SetupModel does not model pyautogui.{name}")
        printed = ""
        for name, arguments in _CALL.findall(program):
            if name in ("moveTo", "click") and arguments:
                x, y = (int(value) for value in arguments.split(",")[:2])
                self.pointer = (x, y)
            if name in _ACKNOWLEDGED_CALLS and self.page_loaded:
                self.page_acknowledged += _ACKNOWLEDGED_CALLS[name]
        if "pyautogui.position()" in program and "print(" in program:
            printed = json.dumps({"parked": list(self.pointer)}, separators=(",", ":")) + "\n"
        return printed


__all__ = ["FakeBaseTask", "SetupModel", "install_fake_osworld"]
