"""The guest-check driver records provider_closed from what happened, not unconditionally.

The 2026-11-11 driver set ``results["provider_closed"] = True`` after ``env.close()`` in a
``finally`` block, so the field said nothing a reader could not assume. These tests drive the
successor's close bookkeeping without booting anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scripts import osworld_guest_check as driver


class _Env:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


def test_close_that_returns_is_recorded_as_returned() -> None:
    env = _Env(None)

    assert driver._close(env) == {"attempted": True, "returned": True, "error": None}  # type: ignore[arg-type]
    assert env.closed


def test_close_that_raises_is_recorded_with_its_error_and_not_raised() -> None:
    record = driver._close(_Env(RuntimeError("docker stop failed")))  # type: ignore[arg-type]

    assert record["attempted"] is True and record["returned"] is False
    assert "docker stop failed" in record["error"]


def _patch_parts(monkeypatch: pytest.MonkeyPatch, *, cases_close: dict[str, Any]) -> None:
    ok = {"attempted": True, "returned": True, "error": None}
    monkeypatch.setattr(driver, "_provenance", lambda guest_image: {"git_revision": "x"})
    monkeypatch.setattr(
        driver, "_run_resets", lambda image, results: results.update(resets={"provider_close": ok})
    )
    monkeypatch.setattr(
        driver,
        "_run_golden",
        lambda image, output, results: results.update(golden={"provider_close": ok}),
    )
    monkeypatch.setattr(
        driver,
        "_run_cases",
        lambda image, results, deadline: results.update(cases={"provider_close": cases_close}),
    )
    monkeypatch.setattr(
        driver,
        "_run_oracle",
        lambda image, results, deadline: results.update(oracle={"skipped": "time budget"}),
    )


class _Task:
    def read_input_acknowledgements(self, env: Any) -> int:
        return 0


class _DesktopEnv:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = str(cache_dir)


class _Backend:
    """Writes park output where OSWorld's setup does: ``desktop-env/<task id>/``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._env: _DesktopEnv | None = None
        self._task = _Task()

    def reset(self, seed: int) -> None:
        task_dir = self.root / "desktop-env" / f"vf-{seed}"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "pixelgym-vendor-form-pointer-park.json").write_text(
            f'{{"parked":[{seed},758]}}\n', encoding="utf-8"
        )
        (task_dir / "pixelgym-vendor-form-pointer-park-error.txt").write_text("", encoding="utf-8")
        self._env = _DesktopEnv(task_dir)


class _GymEnv:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend

    def reset(self, seed: int) -> tuple[Any, dict[str, Any]]:
        self.backend.reset(seed)
        return np.zeros((2, 2, 3), dtype=np.uint8), {}

    def close(self) -> None:
        pass


def test_reset_records_the_park_output_from_the_per_task_setup_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 2026-11-12 driver read desktop-env/ instead of desktop-env/<task id>/ and recorded
    # every park report as missing although setup had read and checked it.
    monkeypatch.setattr(driver, "OSWorldBackend", lambda config: _Backend(tmp_path))
    monkeypatch.setattr(driver, "PixelGuiEnv", _GymEnv)
    results: dict[str, Any] = {}

    driver._run_resets(tmp_path / "guest.qcow2", results)

    reports = [record["pointer_park"] for record in results["resets"]["records"]]
    assert [report["pointer-park.json"] for report in reports] == [
        f'{{"parked":[{seed},758]}}\n' for seed in driver.RESET_SEEDS
    ]
    assert all(report["pointer-park-error.txt"] == "" for report in reports)


def test_park_report_without_a_desktop_env_records_missing_output() -> None:
    assert driver._park_report(None) == {"pointer-park.json": None, "pointer-park-error.txt": None}


@pytest.mark.parametrize(
    ("cases_close", "closed", "status"),
    [
        ({"attempted": True, "returned": True, "error": None}, True, 0),
        ({"attempted": True, "returned": False, "error": "boom"}, False, 1),
    ],
)
def test_provider_closed_is_the_conjunction_of_observed_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cases_close: dict[str, Any],
    closed: bool,
    status: int,
) -> None:
    _patch_parts(monkeypatch, cases_close=cases_close)
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"placeholder")

    exit_status = driver.main([str(tmp_path / "out"), "--guest-image", str(image)])

    results = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["provider_closed"] is closed
    assert exit_status == status
