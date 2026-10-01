"""Failure-mode tests for the OSWorld adapter boundary, driven by explicit fakes.

No Docker, OSWorld, network, or real sleeps: the provider is a scripted fake and
the stabilization loop runs against an injected deterministic clock.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig, OSWorldBackendError
from pixelgym.env import PixelGuiEnv
from pixelgym.tasks.vendor_form import generator

WIDTH, HEIGHT = 4, 3
_DEFAULT_STEP = object()


def _png(value: int = 17, *, width: int = WIDTH, height: int = HEIGHT, mode: str = "RGB") -> bytes:
    shape: tuple[int, ...] = (height, width, 3) if mode == "RGB" else (height, width)
    output = io.BytesIO()
    Image.fromarray(np.full(shape, value, dtype=np.uint8)).save(output, format="PNG")
    return output.getvalue()


class FakeClock:
    """Deterministic replacement for the adapter's ``time`` module."""

    def __init__(self, tick: float) -> None:
        self.now = 0.0
        self.tick = tick
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        value = self.now
        self.now += self.tick
        return value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class ScriptedController:
    """Returns queued screenshot payloads, repeating the last one when exhausted."""

    def __init__(self, frames: list[Any]) -> None:
        self.frames = list(frames)

    def get_screenshot(self) -> Any:
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]


class ScriptedTask:
    bundle_sha256 = "bundle-sha"

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.submissions: list[Any] = []

    def read_privileged_state(self, _env: Any) -> dict[str, Any]:
        return {"task": self.record, "submissions": []}

    def read_submissions(self, _env: Any) -> list[Any]:
        return list(self.submissions)


class ScriptedDesktopEnv:
    """Fake provider whose reset observation, screenshots and step results are scripted."""

    def __init__(self) -> None:
        self.reset_observation: Any = {"screenshot": _png()}
        self.controller = ScriptedController([_png()])
        self.step_result: Any = _DEFAULT_STEP
        self.step_error: Exception | None = None
        self.actions: list[tuple[dict[str, Any], float]] = []
        self.closed = False

    def reset(self, task_config: Any, seed: int) -> Any:
        return self.reset_observation

    def step(self, action: dict[str, Any], pause: float) -> Any:
        self.actions.append((action, pause))
        if self.step_error is not None:
            raise self.step_error
        if self.step_result is not _DEFAULT_STEP:
            return self.step_result
        return ({"screenshot": _png()}, 0, False, {})

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock(tick=1.0)
    monkeypatch.setattr("pixelgym.backends.osworld.time", clock)
    return clock


@pytest.fixture
def provider() -> ScriptedDesktopEnv:
    return ScriptedDesktopEnv()


@pytest.fixture
def task(monkeypatch: pytest.MonkeyPatch) -> ScriptedTask:
    value = ScriptedTask(generator.generate_task(7))
    monkeypatch.setattr(
        "pixelgym.backends.osworld.create_osworld_task",
        lambda seed, cache_dir: (value, generator.generate_task(seed)),
    )
    return value


def _backend(
    tmp_path: Path, provider: ScriptedDesktopEnv, *, timeout: float = 5.0
) -> OSWorldBackend:
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    return OSWorldBackend(
        OSWorldBackendConfig(
            guest_image_path=image,
            cache_dir=tmp_path / "cache",
            width=WIDTH,
            height=HEIGHT,
            stabilization_timeout=timeout,
            stabilization_poll_interval=0.5,
            noop_seconds=0,
        ),
        desktop_env_factory=lambda **_kwargs: provider,
    )


def _noop() -> dict[str, int]:
    return {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}


def test_scripted_fakes_drive_the_valid_path(tmp_path, fake_clock, provider, task):
    """Fidelity check: the same fakes and clock yield a real frame and zero reward."""
    env = PixelGuiEnv(_backend(tmp_path, provider))
    observation, info = env.reset(seed=7)

    assert observation.shape == (HEIGHT, WIDTH, 3)
    assert observation.dtype == np.uint8
    assert np.all(observation == 17)
    assert set(info) == {"episode_id"}

    _obs, reward, terminated, truncated, _info = env.step(_noop())
    assert (reward, terminated, truncated) == (0.0, False, False)
    assert provider.actions == [({"action_type": "WAIT"}, 0)]


def test_stabilization_timeout_raises_naming_the_timeout(tmp_path, fake_clock, provider, task):
    """Failure mode: frames never stabilize; reset raises with the timeout and raw diff."""
    provider.reset_observation = {"screenshot": _png(0)}
    provider.controller = ScriptedController([_png(i % 2 * 200) for i in range(1, 100)])
    backend = _backend(tmp_path, provider, timeout=3.0)

    with pytest.raises(OSWorldBackendError, match=r"within 3\.0s") as excinfo:
        backend.reset(7)

    message = str(excinfo.value)
    assert "12 differing pixels" in message
    assert "maximum channel delta 200" in message
    assert fake_clock.sleeps
    assert all(value == 0.5 for value in fake_clock.sleeps)
    assert provider.closed is True


def test_stabilization_timeout_after_step_emits_no_observation(
    tmp_path, fake_clock, provider, task
):
    """Failure mode: an unstable post-action frame raises instead of returning a frame."""
    env = PixelGuiEnv(_backend(tmp_path, provider))
    env.reset(seed=7)
    provider.controller = ScriptedController([_png(i % 2 * 50) for i in range(100)])

    with pytest.raises(OSWorldBackendError, match="did not become bitwise stable"):
        env.step(_noop())


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (None, "must be bytes, got NoneType"),
        ("not bytes", "must be bytes, got str"),
        (np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8), "must be bytes, got ndarray"),
        (b"\x89PNG\r\n\x1a\n-truncated", "could not decode"),
        (b"", "could not decode"),
        (_png()[:40], "could not decode"),
        (_png(width=WIDTH + 1), "does not match configured"),
        (_png(height=HEIGHT - 1), "does not match configured"),
    ],
    ids=["none", "str", "ndarray", "bad-png", "empty", "truncated-png", "wide", "short"],
)
def test_malformed_controller_frame_is_rejected(
    tmp_path, fake_clock, provider, task, payload, match
):
    """Failure mode: a malformed frame raises; no partial or zero frame is substituted."""
    provider.controller = ScriptedController([payload])

    with pytest.raises(OSWorldBackendError, match=match):
        _backend(tmp_path, provider).reset(7)
    assert provider.closed is True


def test_grayscale_frame_of_correct_size_converts_to_exact_rgb(
    tmp_path, fake_clock, provider, task
):
    """Boundary: a decodable single-channel PNG of the right size yields exact RGB pixels."""
    provider.reset_observation = {"screenshot": _png(90, mode="L")}
    provider.controller = ScriptedController([_png(90, mode="L")])
    backend = _backend(tmp_path, provider)
    backend.reset(7)

    observation = backend.screenshot()
    assert observation.shape == (HEIGHT, WIDTH, 3)
    assert np.all(observation == 90)


@pytest.mark.parametrize(
    ("observation", "match"),
    [
        (None, "not a mapping"),
        ([_png()], "not a mapping"),
        ({}, "must be bytes, got NoneType"),
        ({"screenshot": "png"}, "must be bytes, got str"),
    ],
    ids=["none", "list", "missing-screenshot", "str-screenshot"],
)
def test_malformed_reset_observation_is_rejected(
    tmp_path, fake_clock, provider, task, observation, match
):
    """Failure mode: a malformed reset observation never reaches the agent."""
    provider.reset_observation = observation

    with pytest.raises(OSWorldBackendError, match=match):
        _backend(tmp_path, provider).reset(7)
    assert provider.closed is True


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (None, "unexpected result shape"),
        ({"screenshot": b""}, "unexpected result shape"),
        (({"screenshot": _png()}, 0, False), "unexpected result shape"),
        (({"screenshot": _png()}, 0, False, {}, "extra"), "unexpected result shape"),
        ([{"screenshot": _png()}, 0, False, {}], "unexpected result shape"),
        ((None, 0, False, {}), "not a mapping"),
        (({}, 0, False, {}), "must be bytes, got NoneType"),
        (({"screenshot": 7}, 0, False, {}), "must be bytes, got int"),
    ],
    ids=[
        "none",
        "bare-mapping",
        "three-tuple",
        "five-tuple",
        "list",
        "observation-none",
        "missing-screenshot",
        "int-screenshot",
    ],
)
def test_malformed_step_result_raises_without_reward(
    tmp_path, fake_clock, provider, task, result, match
):
    """Failure mode: a malformed step result raises; no reward or step count is emitted."""
    backend = _backend(tmp_path, provider)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    provider.step_result = result

    with pytest.raises(OSWorldBackendError, match=match):
        env.step(_noop())
    assert backend.structured_action_count == 0


def test_provider_step_reward_and_status_are_ignored(tmp_path, fake_clock, provider, task):
    """Invariant 6: a provider-reported reward, done flag or status never becomes reward."""
    env = PixelGuiEnv(_backend(tmp_path, provider))
    env.reset(seed=7)
    provider.step_result = ({"screenshot": _png()}, 1.0, True, {"status": "success"})

    _obs, reward, terminated, truncated, info = env.step(_noop())

    assert (reward, terminated, truncated) == (0.0, False, False)
    assert "status" not in info


def test_provider_step_exception_is_wrapped(tmp_path, fake_clock, provider, task):
    """Failure mode: a provider step error surfaces as a typed adapter error."""
    backend = _backend(tmp_path, provider)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    provider.step_error = ConnectionError("guest went away")

    with pytest.raises(OSWorldBackendError, match="CLICK failed: guest went away"):
        env.step({"action_type": int(ActionType.CLICK), "x": 1, "y": 1, "key": 0})
    assert backend.structured_action_count == 0


@pytest.mark.parametrize("key", [-1, len(KEY_ALLOWLIST), 10_000])
def test_key_index_outside_allowlist_never_reaches_provider(
    tmp_path, fake_clock, provider, task, key
):
    """Invariants 2 and 4: an out-of-allowlist KEY index is rejected pre-backend."""
    backend = _backend(tmp_path, provider)
    env = PixelGuiEnv(backend)
    env.reset(seed=7)

    with pytest.raises(ValueError):
        env.step({"action_type": int(ActionType.KEY), "x": 0, "y": 0, "key": key})
    assert provider.actions == []
    assert backend.structured_action_count == 0


@pytest.mark.parametrize(
    "key", ["", "ab", "\n", "\x00", "F1", "ctrl+c", "Escape", None, 65], ids=repr
)
def test_unmappable_key_is_rejected_by_adapter_before_provider(
    tmp_path, fake_clock, provider, task, key
):
    """Invariant 4: a key the guest cannot map raises before any provider step."""
    backend = _backend(tmp_path, provider)
    backend.reset(7)

    with pytest.raises(OSWorldBackendError, match="not a supported PixelGym key"):
        backend.key(key)
    assert provider.actions == []
    assert backend.structured_action_count == 0


def test_every_allowlisted_key_maps_to_one_structured_action(tmp_path, fake_clock, provider, task):
    """Invariant 2: every versioned allowlist entry maps to one PRESS or one-char TYPING."""
    backend = _backend(tmp_path, provider)
    backend.reset(7)

    for key in KEY_ALLOWLIST:
        backend.key(key)

    assert len(provider.actions) == len(KEY_ALLOWLIST)
    for action, _pause in provider.actions:
        assert action["action_type"] in {"PRESS", "TYPING"}
        if action["action_type"] == "TYPING":
            assert len(action["parameters"]["text"]) == 1
