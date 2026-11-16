"""Unit tests for the guest reset-determinism driver, against the fake guest setup model.

No Docker, VM, browser, or network. A `DesktopEnv` stand-in runs the real vendor-form guest setup
programs against `SetupModel` on every reset (so a setup step that clicked the page would show up
as an acknowledgement the backend rejects), serves small PNG frames, and can be told to fail a
reset, change a frame, raise on close, or be interrupted.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.tasks.vendor_form import generator
from pixelgym.tasks.vendor_form.osworld_task import create_osworld_task
from scripts import osworld_reset_determinism as driver
from tests.support.osworld_setup_model import SetupModel, install_fake_osworld

WIDTH, HEIGHT = 4, 3


def _png(value: int, *, poke: bool = False) -> bytes:
    frame = np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)
    if poke:
        frame[0, 0] = (value, value, value + 9)
    out = io.BytesIO()
    Image.fromarray(frame).save(out, format="PNG")
    return out.getvalue()


class _Guest:
    """Task surface the backend reads: privileged state and the page's acknowledgement count."""

    bundle_sha256 = "bundle-sha"

    def __init__(self, seed: int) -> None:
        self.record = generator.generate_task(seed)
        self.acknowledged = 0

    def read_privileged_state(self, _env: Any) -> dict[str, Any]:
        return {"task": self.record, "submissions": []}

    def read_input_acknowledgements(self, _env: Any) -> int:
        return self.acknowledged

    def read_submissions(self, _env: Any) -> list[Any]:
        return []


class _Controller:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    def get_screenshot(self) -> bytes:
        return self.provider.frame


class _Provider:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.controller = _Controller(self)
        self.frame = _png(17)
        self.cache_dir = world.setup_dir

    def reset(self, task_config: Any, seed: int) -> dict[str, bytes]:
        self.world.resets += 1
        if self.world.resets in self.world.fail_on:
            raise RuntimeError("guest setup failed")
        if self.world.resets in self.world.interrupt_on:
            raise KeyboardInterrupt
        setup_task, record = create_osworld_task(seed, cache_dir=self.world.setup_dir)
        model = SetupModel(self.world.setup_dir, record)
        setup_task.setup(model)
        task_config.acknowledged = model.page_acknowledged
        poke = self.world.resets in self.world.changed_frame_on
        self.frame = _png(17 + seed % 50, poke=poke)
        return {"screenshot": self.frame}

    def close(self) -> None:
        self.world.closes += 1
        if self.world.close_raises:
            raise RuntimeError("container would not stop")


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.setup_dir = tmp_path / "setup"
        self.setup_dir.mkdir()
        self.resets = 0
        self.closes = 0
        self.fail_on: set[int] = set()
        self.interrupt_on: set[int] = set()
        self.changed_frame_on: set[int] = set()
        self.close_raises = False


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    install_fake_osworld(monkeypatch)
    world = _World(tmp_path)
    monkeypatch.setattr(
        "pixelgym.backends.osworld.create_osworld_task",
        lambda seed, cache_dir: (_Guest(seed), generator.generate_task(seed)),
    )
    monkeypatch.setattr(driver, "bundle_digests", lambda seeds: {str(s): f"b{s}" for s in seeds})
    return world


def _factory(world: _World, tmp_path: Path) -> Any:
    def make(image: Path) -> OSWorldBackend:
        return OSWorldBackend(
            OSWorldBackendConfig(
                guest_image_path=image,
                cache_dir=tmp_path / "cache",
                width=WIDTH,
                height=HEIGHT,
                stabilization_poll_interval=0,
                input_ack_poll_interval=0,
                noop_seconds=0,
            ),
            desktop_env_factory=lambda **_kwargs: _Provider(world),
        )

    return make


def _run(world: _World, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    return driver.run(
        tmp_path / "out",
        image,
        kwargs.pop("seeds", [7, 11]),
        kwargs.pop("resets", 3),
        backend_factory=_factory(world, tmp_path),
        **kwargs,
    )


def _stored(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))


def test_records_every_reset_and_closes_the_provider_once(world: _World, tmp_path: Path) -> None:
    results = _run(world, tmp_path)
    assert [len(s["resets"]) for s in results["per_seed"]] == [3, 3]
    assert all(len(s["distinct_frame_sha256"]) == 1 for s in results["per_seed"])
    assert all(r["acknowledged_after_reset"] == 0 for s in results["per_seed"] for r in s["resets"])
    assert results["provider_close"] == {
        "attempted": True,
        "returned": True,
        "error": None,
        "suppressed_close_errors": [],
    }
    assert results["provider_closed"] is True and world.closes == 1
    meta = results["integration_metadata"]
    assert meta["upstream_tag"] and meta["provider"] and meta["python_version"]
    assert meta["screen_size"] == [WIDTH, HEIGHT] and meta["guest_artifact_sha256"]
    assert results["provenance"]["guest_bundle_sha256_by_seed"] == {"7": "b7", "11": "b11"}
    assert _stored(tmp_path) == json.loads(json.dumps(results, default=str))


def test_a_changed_frame_is_recorded_with_raw_differences(world: _World, tmp_path: Path) -> None:
    world.changed_frame_on = {2}
    results = _run(world, tmp_path, seeds=[7])
    (seed,) = results["per_seed"]
    assert len(seed["distinct_frame_sha256"]) == 2
    assert [p["resets"] for p in seed["differing_frame_pairs"]] == [[0, 1], [1, 2]]
    assert seed["differing_frame_pairs"][0]["differing_pixel_count"] == 1
    assert seed["differing_frame_pairs"][0]["max_per_channel_delta"] == 9
    assert (tmp_path / "out" / "seed-7-reset-1.png").is_file()


def test_a_failed_reset_is_a_result_and_the_close_is_still_recorded(
    world: _World, tmp_path: Path
) -> None:
    world.fail_on = {2}
    results = _run(world, tmp_path, seeds=[7], resets=3)
    (seed,) = results["per_seed"]
    assert [e["reset_index"] for e in seed["reset_errors"]] == [1]
    assert "guest setup failed" in seed["reset_errors"][0]["error"]
    assert [r["reset_index"] for r in seed["resets"]] == [0, 2]
    assert results["provider_closed"] is True


def test_close_failure_is_recorded_from_what_happened(world: _World, tmp_path: Path) -> None:
    world.close_raises = True
    results = _run(world, tmp_path)
    assert results["provider_closed"] is False
    assert "container would not stop" in results["provider_close"]["error"]
    assert _stored(tmp_path)["provider_closed"] is False


def test_exit_status_is_nonzero_when_the_close_failed(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.close_raises = True
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")
    monkeypatch.setattr(driver, "default_backend", _factory(world, tmp_path))
    argv = [str(tmp_path / "out2"), "--guest-image", str(image), "--seeds", "7", "--resets", "1"]
    assert driver.main(argv) == 1
    world.close_raises = False
    assert driver.main([str(tmp_path / "out3"), *argv[1:]]) == 0


def test_suppressed_close_error_after_a_failed_reset_counts_as_not_closed(
    world: _World, tmp_path: Path
) -> None:
    world.fail_on = {1}
    world.close_raises = True
    results = _run(world, tmp_path, seeds=[7], resets=1)
    assert results["provider_close"]["suppressed_close_errors"]
    assert results["provider_closed"] is False


def test_interrupt_still_closes_and_writes(world: _World, tmp_path: Path) -> None:
    world.interrupt_on = {2}
    with pytest.raises(KeyboardInterrupt):
        _run(world, tmp_path, seeds=[7], resets=3)
    stored = _stored(tmp_path)
    assert stored["provider_close"]["attempted"] is True
    assert [r["reset_index"] for r in stored["per_seed"][0]["resets"]] == [0]


def test_time_budget_skips_resets_explicitly(world: _World, tmp_path: Path) -> None:
    ticks = iter(range(0, 10_000, 100))
    results = _run(
        world, tmp_path, seeds=[7, 11], resets=3, budget_minutes=5, clock=lambda: next(ticks)
    )
    recorded = sum(len(s.get("resets", [])) for s in results["per_seed"])
    assert recorded + len(results["skipped"]) == 6
    assert results["skipped"] and all(s["reason"] == "time budget" for s in results["skipped"])
    assert results["provider_closed"] is True
