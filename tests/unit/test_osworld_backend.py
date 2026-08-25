"""Thin-fake tests for OSWorldBackend's adapter and cleanup boundaries."""

from __future__ import annotations

import io
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.osworld import (
    ARM64_RUNTIME_BASE_DIGEST,
    ARM64_RUNTIME_IMAGE_REFERENCE,
    RUNTIME_IMAGE_REFERENCE,
    OSWorldBackend,
    OSWorldBackendConfig,
    OSWorldBackendError,
    _portable_docker_available_port,
)
from pixelgym.env import PixelGuiEnv
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form import generator


def _png(width=4, height=3, value=17):
    frame = np.full((height, width, 3), value, dtype=np.uint8)
    output = io.BytesIO()
    Image.fromarray(frame).save(output, format="PNG")
    return output.getvalue()


class _FakeTask:
    bundle_sha256 = "bundle-sha"

    def __init__(self, record):
        self.record = record
        self.submissions = []

    def read_privileged_state(self, _env):
        return {"task": self.record, "submissions": []}

    def read_submissions(self, _env):
        return list(self.submissions)


class _FakeController:
    def __init__(self, frame):
        self.frame = frame
        self.screenshot_calls = 0

    def get_screenshot(self):
        self.screenshot_calls += 1
        return self.frame

    def run_bash_script(self, script, timeout):
        assert script == "df -B1 --output=size,avail,pcent / | tail -n 1"
        assert timeout == 15
        return {"returncode": 0, "output": "53687091200 26843545600 50%\n", "error": ""}


class _FakeDesktopEnv:
    def __init__(self, frame, **kwargs):
        self.kwargs = kwargs
        self.controller = _FakeController(frame)
        self.actions = []
        self.closed = False

    def reset(self, task_config, seed):
        self.task = task_config
        self.seed = seed
        return {"screenshot": self.controller.frame, "accessibility_tree": None, "terminal": None}

    def step(self, action, pause):
        self.actions.append((action, pause))
        return ({"screenshot": self.controller.frame}, 0, False, {})

    def close(self):
        self.closed = True


@pytest.fixture
def backend(monkeypatch, tmp_path):
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    record = generator.generate_task(7)
    task = _FakeTask(record)
    monkeypatch.setattr(
        "pixelgym.backends.osworld.create_osworld_task",
        lambda seed, cache_dir: (task, generator.generate_task(seed)),
    )
    created = []

    def factory(**kwargs):
        env = _FakeDesktopEnv(_png(), **kwargs)
        created.append(env)
        return env

    value = OSWorldBackend(
        OSWorldBackendConfig(
            guest_image_path=image,
            cache_dir=tmp_path / "cache",
            width=4,
            height=3,
            stabilization_poll_interval=0,
            noop_seconds=0,
        ),
        desktop_env_factory=factory,
    )
    return value, task, created


def test_public_environment_reset_returns_only_pixels_and_task_id(backend):
    osworld, _task, created = backend
    env = PixelGuiEnv(osworld)

    observation, info = env.reset(seed=7)

    assert observation.shape == (3, 4, 3)
    assert observation.dtype == np.uint8
    assert set(info) == {"task_id"}
    assert created[0].kwargs["action_space"] == "computer_13"
    assert created[0].kwargs["require_a11y_tree"] is False
    assert created[0].kwargs["require_terminal"] is False
    assert created[0].kwargs["volume_size"] == 50


def test_v5_live_reconnect_checkpoint_verifies_exact_osworld_session_state(backend):
    osworld, _task, _created = backend
    osworld.reset(7)
    checkpoint = osworld.checkpoint()
    record = osworld.environment_resume_record(step_count=0)

    osworld.restore(checkpoint)
    osworld.verify_resume_record(record, step_count=0)

    osworld.click(1, 1)
    with pytest.raises(OSWorldBackendError, match="cannot prove|binding mismatch"):
        osworld.restore(checkpoint)


def test_actions_translate_only_to_bounded_structured_actions(backend):
    osworld, _task, created = backend
    env = PixelGuiEnv(osworld)
    env.reset(seed=7)

    env.step({"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0})
    env.step({"action_type": ActionType.CLICK, "x": 2, "y": 1, "key": 0})
    env.step(
        {
            "action_type": ActionType.KEY,
            "x": 0,
            "y": 0,
            "key": KEY_ALLOWLIST.index("A"),
        }
    )
    env.step(
        {
            "action_type": ActionType.KEY,
            "x": 0,
            "y": 0,
            "key": KEY_ALLOWLIST.index("Tab"),
        }
    )

    assert created[0].actions == [
        ({"action_type": "WAIT"}, 0),
        (
            {
                "action_type": "CLICK",
                "parameters": {"button": "left", "x": 2, "y": 1},
            },
            0,
        ),
        ({"action_type": "TYPING", "parameters": {"text": "A"}}, 0),
        ({"action_type": "PRESS", "parameters": {"key": "tab"}}, 0),
    ]
    assert all("command" not in action for action, _pause in created[0].actions)
    assert osworld.structured_action_count == 4


def test_invalid_action_never_reaches_osworld(backend):
    osworld, _task, created = backend
    env = PixelGuiEnv(osworld)
    env.reset(seed=7)

    with pytest.raises(ValueError, match="out of range"):
        env.step({"action_type": ActionType.CLICK, "x": 4, "y": 0, "key": 0})

    assert osworld.structured_action_count == 0
    assert created[0].actions == []


def test_privileged_submission_is_the_only_reward_source(backend):
    osworld, task, _created = backend
    env = PixelGuiEnv(osworld)
    env.reset(seed=7)
    task.submissions = [
        Submission(
            task_id=task.record["task_id"],
            seed=7,
            values=task.record["fields"],
            submitted_at_step=1,
        )
    ]

    _observation, reward, terminated, truncated, _info = env.step(
        {"action_type": ActionType.NOOP, "x": 0, "y": 0, "key": 0}
    )

    assert (reward, terminated, truncated) == (1.0, True, False)


def test_guest_root_disk_probe_is_validation_only(backend):
    osworld, _task, _created = backend
    env = PixelGuiEnv(osworld)
    _observation, info = env.reset(seed=7)

    disk = osworld.read_guest_root_disk()

    assert disk == {
        "size_bytes": 53_687_091_200,
        "available_bytes": 26_843_545_600,
        "used_percent": 50,
    }
    assert set(info) == {"task_id"}


def test_close_is_idempotent_and_closes_provider(backend):
    osworld, _task, created = backend
    osworld.reset(7)

    osworld.close()
    osworld.close()

    assert created[0].closed is True


def test_integration_metadata_records_release_runtime_and_python(backend):
    osworld, _task, _created = backend
    osworld.reset(7)

    metadata = osworld.integration_metadata()

    assert metadata["upstream_tag"] == "v2026.06.24"
    assert metadata["upstream_commit"] == "2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6"
    assert metadata["runtime_image"] == osworld.config.runtime_image_reference
    assert metadata["provider"] == "docker-local"
    assert metadata["screen_size"] == [4, 3]
    assert metadata["python_version"]
    assert metadata["task_bundle_sha256"] == "bundle-sha"


def test_portable_docker_allocator_skips_host_and_docker_ports(monkeypatch):
    host_port = 50_000
    docker_port = host_port + 1
    container = types.SimpleNamespace(
        attrs={"NetworkSettings": {"Ports": {"5000/tcp": [{"HostPort": str(docker_port)}]}}}
    )
    provider = types.SimpleNamespace(
        client=types.SimpleNamespace(containers=types.SimpleNamespace(list=lambda: [container]))
    )

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, address):
            if address[1] == host_port:
                raise OSError("occupied")

    monkeypatch.setattr("pixelgym.backends.osworld.socket.socket", lambda *_args: Probe())

    selected = _portable_docker_available_port(provider, host_port)

    assert selected == docker_port + 1


def test_runtime_reference_selects_native_host_only_for_apple_silicon(monkeypatch, tmp_path):
    monkeypatch.setattr("pixelgym.backends.osworld.platform.system", lambda: "Darwin")
    monkeypatch.setattr("pixelgym.backends.osworld.platform.machine", lambda: "arm64")

    config = OSWorldBackendConfig(guest_image_path=tmp_path / "guest.qcow2")

    assert config.runtime_image_reference == ARM64_RUNTIME_IMAGE_REFERENCE

    monkeypatch.setattr("pixelgym.backends.osworld.platform.system", lambda: "Linux")
    linux_config = OSWorldBackendConfig(guest_image_path=tmp_path / "guest.qcow2")
    assert linux_config.runtime_image_reference == RUNTIME_IMAGE_REFERENCE


def test_arm64_runtime_dockerfile_pins_base_and_read_only_snapshot_mode():
    dockerfile = (Path(__file__).parents[2] / "docker" / "osworld-arm64" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert f"FROM qemux/qemu@{ARM64_RUNTIME_BASE_DIGEST}" in dockerfile
    assert "FROM scratch" in dockerfile
    assert "VOLUME /storage" not in dockerfile
    assert 'detectType "/System.qcow2"' in dockerfile
    assert 'ENV ARGUMENTS="-snapshot"' in dockerfile


def test_reset_failure_closes_provider(monkeypatch, tmp_path):
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    created = []

    class Broken(_FakeDesktopEnv):
        def reset(self, task_config, seed):
            raise RuntimeError("provider reset broke")

    def factory(**kwargs):
        env = Broken(_png(), **kwargs)
        created.append(env)
        return env

    monkeypatch.setattr(
        "pixelgym.backends.osworld.create_osworld_task",
        lambda seed, cache_dir: (
            _FakeTask(generator.generate_task(seed)),
            generator.generate_task(seed),
        ),
    )
    osworld = OSWorldBackend(
        OSWorldBackendConfig(guest_image_path=image, width=4, height=3),
        desktop_env_factory=factory,
    )

    with pytest.raises(RuntimeError, match="provider reset broke"):
        osworld.reset(7)

    assert created[0].closed is True
