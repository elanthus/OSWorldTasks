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


class _ClosingBackend:
    def __init__(self, suppressed: list[str] | None = None) -> None:
        self.suppressed_close_errors = list(suppressed or [])


def test_close_that_returns_is_recorded_as_returned() -> None:
    env = _Env(None)

    assert driver._close(env, _ClosingBackend()) == {  # type: ignore[arg-type]
        "attempted": True,
        "returned": True,
        "error": None,
        "suppressed_close_errors": [],
    }
    assert env.closed


def test_close_that_raises_is_recorded_with_its_error_and_not_raised() -> None:
    record = driver._close(_Env(RuntimeError("docker stop failed")), _ClosingBackend())  # type: ignore[arg-type]

    assert record["attempted"] is True and record["returned"] is False
    assert "docker stop failed" in record["error"]


def test_a_close_failure_suppressed_by_a_failed_reset_is_not_reported_as_returned() -> None:
    # The failed reset already closed the provider and kept only the reset error; the driver's own
    # close then finds no provider and returns. The earlier failure must still be reported.
    backend = _ClosingBackend(["failed to close OSWorld provider: docker stop failed"])

    record = driver._close(_Env(None), backend)  # type: ignore[arg-type]

    assert record["returned"] is False and record["error"] is None
    assert record["suppressed_close_errors"] == [
        "failed to close OSWorld provider: docker stop failed"
    ]


def test_backend_keeps_the_close_error_that_failure_cleanup_suppresses() -> None:
    from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig

    class _Provider:
        def close(self) -> None:
            raise RuntimeError("docker stop failed")

    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=Path("guest.qcow2")))
    backend._env = _Provider()

    backend._close_after_failure()

    assert backend._env is None
    assert backend.suppressed_close_errors == [
        "failed to close OSWorld provider: docker stop failed"
    ]
    backend.close()  # a later close finds no provider and returns
    assert len(backend.suppressed_close_errors) == 1


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
        self.suppressed_close_errors: list[str] = []

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
