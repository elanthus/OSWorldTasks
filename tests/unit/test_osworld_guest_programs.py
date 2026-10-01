"""Pin the WP15 move of guest programs from string literals to package files.

Each rendered program must be byte-identical to the literal the module built
at revision 74aaa75 for the same inputs. The old literals are read with
``git show`` (local, offline); the test skips if that revision is unavailable.
This pin may be removed once the migration is no longer under review.
"""

from __future__ import annotations

import ast
import subprocess
import types
from pathlib import Path

import pytest

from pixelgym.tasks.vendor_form import osworld_task
from pixelgym.tasks.vendor_form.browser_contract import GUEST_VIEWPORT_SIZE

_REVISION = "74aaa7557247049739fb60041c6d5b422dc88b9f"
_REPO = Path(__file__).resolve().parents[2]
_HEREDOC_HEAD = "python3 - <<'PY'\n"
_HEREDOC_TAIL = "PY\n"
_SEED = 4242
_SCREEN = (1920, 1080)


def _old_literals() -> dict[str, str]:
    try:
        source = subprocess.run(
            ["git", "show", f"{_REVISION}:pixelgym/tasks/vendor_form/osworld_task.py"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git revision {_REVISION} is not available")
    width, height = GUEST_VIEWPORT_SIZE
    namespace = {
        "APP_URL": osworld_task.APP_URL,
        "display_width": width,
        "display_height": height,
        "self": types.SimpleNamespace(_record={"seed": _SEED}),
        "setup_controller": types.SimpleNamespace(
            screen_width=_SCREEN[0], screen_height=_SCREEN[1]
        ),
    }

    def evaluate(node: ast.expr) -> str:
        value = eval(compile(ast.Expression(node), "old", "eval"), namespace)
        assert isinstance(value, str)
        return value

    names = {
        "ready_script": "service_ready",
        "reset_script": "reset",
        "desktop_ready_script": "desktop_ready",
        "display_size_script": "display_size",
        "page_ready_script": "page_ready",
    }
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            if target in names:
                found[names[target]] = evaluate(node.value)
            elif target == "script":
                text = evaluate(node.value)
                key = "privileged_state" if "api/state" in text else "browser_window_state"
                found[key] = text
        if (
            isinstance(node, ast.List)
            and len(node.elts) == 3
            and isinstance(node.elts[1], ast.Constant)
            and node.elts[1].value == "-c"
            and not isinstance(node.elts[2], ast.Name)
        ):
            found["pointer_park"] = evaluate(node.elts[2])
    return found


def _rendered() -> dict[str, str]:
    width, height = GUEST_VIEWPORT_SIZE
    url = osworld_task.APP_URL
    render = osworld_task._guest_program
    return {
        "service_ready": render("service_ready", APP_URL=url),
        "reset": render("reset", APP_URL=url, SEED=_SEED),
        "desktop_ready": render("desktop_ready"),
        "display_size": render("display_size", DISPLAY_WIDTH=width, DISPLAY_HEIGHT=height),
        "page_ready": render("page_ready", APP_URL=url),
        "pointer_park": render("pointer_park", PARK_X=_SCREEN[0] - 10, PARK_Y=_SCREEN[1] - 10),
        "privileged_state": _HEREDOC_HEAD + render("privileged_state", APP_URL=url) + _HEREDOC_TAIL,
        "browser_window_state": _HEREDOC_HEAD
        + render("browser_window_state", APP_URL=url)
        + _HEREDOC_TAIL,
    }


def test_every_former_literal_is_reproduced_byte_for_byte() -> None:
    old = _old_literals()
    new = _rendered()
    assert sorted(old) == sorted(osworld_task._GUEST_PROGRAM_NAMES)
    for name in old:
        assert new[name].encode("utf-8") == old[name].encode("utf-8"), name


def test_guest_program_files_are_package_data() -> None:
    directory = Path(osworld_task.__file__).parent / "guest_programs"
    assert sorted(p.stem for p in directory.glob("*.py")) == sorted(
        osworld_task._GUEST_PROGRAM_NAMES
    )


def test_guest_program_rejects_unknown_and_unfilled_placeholders() -> None:
    with pytest.raises(osworld_task.OSWorldTaskError, match="no placeholder"):
        osworld_task._guest_program("desktop_ready", APP_URL="x")
    with pytest.raises(osworld_task.OSWorldTaskError, match="unfilled placeholder"):
        osworld_task._guest_program("reset", APP_URL="http://x/")
