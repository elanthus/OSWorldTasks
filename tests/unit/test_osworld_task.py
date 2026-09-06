"""OSWorld custom-task tests without importing or starting OSWorld."""

from __future__ import annotations

import json
import shlex
import sys
import types
import zipfile
from pathlib import Path

import pytest

from pixelgym.tasks.vendor_form import browser_contract, generator
from pixelgym.tasks.vendor_form.browser_contract import (
    ChromiumLaunchPath,
    build_chromium_argv,
    chromium_renderer_contract,
)
from pixelgym.tasks.vendor_form.osworld_task import build_guest_bundle, create_osworld_task


class _FakeBaseTask(dict):
    def __init__(self, **overrides):
        super().__init__(overrides)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value


def _install_fake_osworld(monkeypatch, base_task=_FakeBaseTask):
    desktop_env = types.ModuleType("desktop_env")
    task_base = types.ModuleType("desktop_env.task_base")
    task_base.BaseTask = base_task
    desktop_env.task_base = task_base
    monkeypatch.setitem(sys.modules, "desktop_env", desktop_env)
    monkeypatch.setitem(sys.modules, "desktop_env.task_base", task_base)


class _ReadOnlyInstructionBaseTask:
    def __init__(self, *, instruction, **overrides):
        self._instruction = instruction
        for name, value in overrides.items():
            setattr(self, name, value)

    @property
    def instruction(self):
        return self._instruction


def test_guest_bundle_is_byte_reproducible(tmp_path):
    record = generator.generate_task(7)
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"

    first_hash = build_guest_bundle(record, first)
    second_hash = build_guest_bundle(record, second)

    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert json.loads(archive.read("task.json")) == record
        assert all(member.date_time == (1980, 1, 1, 0, 0, 0) for member in archive.infolist())


def test_runtime_task_derives_from_installed_base_task(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)

    task, record = create_osworld_task(7, cache_dir=tmp_path)

    assert isinstance(task, _FakeBaseTask)
    assert task.__class__.__name__ == "VendorFormOSWorldTask"
    assert task.id == record["task_id"]
    assert task.source == "pixelgym-open-vendor-form"
    assert task.bundle_sha256
    assert task._bundle_path.name.endswith(f"-{task.bundle_sha256[:16]}.zip")


def test_runtime_task_preserves_read_only_base_instruction(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch, _ReadOnlyInstructionBaseTask)

    task, _record = create_osworld_task(7, cache_dir=tmp_path, instruction="Read-only base value")

    assert task.instruction == "Read-only base value"


class _EvaluationController:
    def __init__(self, state):
        self.state = state

    def run_bash_script(self, script, timeout):
        assert "/api/state" in script
        assert timeout == 15
        return {"returncode": 0, "output": json.dumps(self.state), "error": ""}


def test_custom_task_uses_host_evaluator_and_structured_result(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    submission = {
        "task_id": record["task_id"],
        "seed": record["seed"],
        "values": record["fields"],
        "submitted_at_step": 1,
        "final": True,
    }
    env = types.SimpleNamespace(
        controller=_EvaluationController({"task": record, "submissions": [submission]})
    )

    result = task.evaluate(env)

    assert result == {
        "success": True,
        "score": 1.0,
        "submitted": True,
        "mismatched_fields": (),
        "task_id_matches": True,
    }


def test_custom_task_end_evaluation_uses_the_latest_submission(monkeypatch, tmp_path):
    """Native OSWorld evaluates once at episode end, so a later invalid event supersedes success."""
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    invalid_values = {**record["fields"], "company_name": ""}
    submissions = [
        {
            "task_id": record["task_id"],
            "seed": record["seed"],
            "values": record["fields"],
            "submitted_at_step": 1,
            "final": True,
        },
        {
            "task_id": record["task_id"],
            "seed": record["seed"],
            "values": invalid_values,
            "submitted_at_step": 2,
            "final": True,
        },
    ]
    env = types.SimpleNamespace(
        controller=_EvaluationController({"task": record, "submissions": submissions})
    )

    result = task.evaluate(env)

    assert result["success"] is False
    assert result["score"] < 1.0
    assert result["mismatched_fields"] == ("company_name",)


def test_privileged_state_identity_mismatch_is_rejected(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, _record = create_osworld_task(7, cache_dir=tmp_path)
    stale = generator.generate_task(8)
    env = types.SimpleNamespace(
        controller=_EvaluationController({"task": stale, "submissions": []})
    )

    with pytest.raises(RuntimeError, match="does not match"):
        task.read_privileged_state(env)


class _SetupController:
    def __init__(self, cache_dir: Path, reset_identity: dict):
        self.cache_dir = str(cache_dir)
        self.screen_width = 1920
        self.screen_height = 1080
        self.reset_identity = reset_identity
        self.downloads = []
        self.commands = []
        self.launches = []
        self.setup_calls = []

    def download(self, files):
        self.downloads.extend(files)

    def execute(self, command, *, stdout="", **kwargs):
        self.commands.append((command, kwargs))
        if stdout:
            value = (
                "PAGE_READY\n"
                if "page-ready" in stdout
                else "DISPLAY_SIZE_READY\n"
                if "display-size" in stdout
                else "DESKTOP_READY\n"
                if "desktop-ready" in stdout
                else "READY\n"
                if "ready" in stdout
                else json.dumps(self.reset_identity)
            )
            (Path(self.cache_dir) / stdout).write_text(value, encoding="utf-8")

    def launch(self, command):
        self.launches.append(command)

    def setup(self, config, use_proxy):
        self.setup_calls.append((config, use_proxy))
        return True


def test_custom_task_setup_accepts_reload_contract_and_opens_browser(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    controller = _SetupController(
        tmp_path,
        # Guest reset body source of truth: test_vendor_form_guest_server.py.
        {
            "task_id": record["task_id"],
            "seed": record["seed"],
            "requires_reload": True,
        },
    )

    task.setup(controller)

    assert controller.downloads[0]["url"].endswith(".zip")
    assert controller.launches[0][0:2] == ["bash", "-lc"]
    assert "/tmp/pixelgym-vendor-form/guest_server.py" in controller.launches[0][2]
    assert "rm -rf /dev/shm/pixelgym-chrome-profile" in controller.commands[0][0][2]
    assert controller.launches[1][0:2] == ["bash", "-lc"]
    guest_command = controller.launches[1][2]
    assert "--user-data-dir=/dev/shm/pixelgym-chrome-profile" in guest_command
    assert not any(
        "/tmp/" in token for token in task.browser_launch_metadata["effective_argv"]
    )
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in controller.launches[1][2]
    assert "http://127.0.0.1:3000/" in controller.launches[1][2]
    assert "--app=http://127.0.0.1:3000/" in guest_command
    assert "--start-fullscreen" in guest_command
    assert "--window-size=1024,768" in guest_command
    assert "--kiosk" not in guest_command
    assert "--start-maximized" not in guest_command
    assert controller.setup_calls == []
    assert any("[g]uest_server.py" in str(command) for command, _kwargs in controller.commands)
    assert any("[g]oogle-chrome" in str(command) for command, _kwargs in controller.commands)
    assert any("xrandr" in str(command) for command, _kwargs in controller.commands)
    assert all("DONE" not in str(command) for command, _kwargs in controller.commands)


def test_guest_launch_contains_each_canonical_renderer_flag_once(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    controller = _SetupController(tmp_path, {"task_id": record["task_id"], "seed": record["seed"]})

    task.setup(controller)

    guest_command = controller.launches[1][2]
    command_tokens = shlex.split(guest_command)
    guest_metadata = task.browser_launch_metadata
    for flag in browser_contract.CHROMIUM_RENDERER_ARGS:
        assert command_tokens.count(flag) == 1
    assert guest_metadata["renderer_contract"] == chromium_renderer_contract()
    assert guest_metadata["presentation_mode"] == "app"
    assert guest_metadata["presentation_mode_fallback_from"] == "kiosk"
    assert "1024x768" in guest_metadata["presentation_mode_reason"]
    assert guest_metadata["effective_argv"] == list(
        build_chromium_argv(ChromiumLaunchPath.OSWORLD_GUEST)
    )
    assert not any(
        flag.startswith("--app=")
        for flag in build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)[1:]
    )


def test_canonical_renderer_change_updates_playwright_and_guest_paths(monkeypatch, tmp_path):
    baseline_identity = chromium_renderer_contract()["identity"]
    monkeypatch.setattr(
        browser_contract,
        "CHROMIUM_RENDERER_ARGS",
        (*browser_contract.CHROMIUM_RENDERER_ARGS, "--renderer-contract-test-flag"),
    )
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    controller = _SetupController(tmp_path, {"task_id": record["task_id"], "seed": record["seed"]})

    task.setup(controller)

    playwright_argv = build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)
    guest_command = controller.launches[1][2]
    assert "--renderer-contract-test-flag" in playwright_argv
    assert shlex.split(guest_command).count("--renderer-contract-test-flag") == 1
    assert task.browser_launch_metadata["renderer_contract"]["identity"] != baseline_identity
    assert task.browser_launch_metadata["renderer_contract"] == chromium_renderer_contract()


def test_chromium_argv_builder_rejects_non_enum_input() -> None:
    with pytest.raises(TypeError, match="ChromiumLaunchPath"):
        build_chromium_argv("osworld-guest")  # type: ignore[arg-type]


def test_custom_task_setup_rejects_reset_identity_mismatch(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, _record = create_osworld_task(7, cache_dir=tmp_path)
    controller = _SetupController(
        tmp_path,
        {"task_id": "vf-stale", "seed": 7, "requires_reload": True},
    )

    with pytest.raises(RuntimeError, match="response mismatch"):
        task.setup(controller)


@pytest.mark.parametrize("requires_reload", [None, False])
def test_custom_task_setup_rejects_missing_or_false_reload_contract(
    monkeypatch, tmp_path, requires_reload
):
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    reset_result = {"task_id": record["task_id"], "seed": record["seed"]}
    if requires_reload is not None:
        reset_result["requires_reload"] = requires_reload
    controller = _SetupController(tmp_path, reset_result)

    with pytest.raises(RuntimeError, match="response mismatch"):
        task.setup(controller)


def test_custom_task_setup_rejects_unexpected_reset_field(monkeypatch, tmp_path):
    _install_fake_osworld(monkeypatch)
    task, record = create_osworld_task(7, cache_dir=tmp_path)
    controller = _SetupController(
        tmp_path,
        {
            "task_id": record["task_id"],
            "seed": record["seed"],
            "requires_reload": True,
            "unexpected": "field",
        },
    )

    with pytest.raises(RuntimeError, match="response mismatch"):
        task.setup(controller)
