"""The guest-check driver records provider_closed from what happened, not unconditionally.

The 2026-11-11 driver set ``results["provider_closed"] = True`` after ``env.close()`` in a
``finally`` block, so the field said nothing a reader could not assume. These tests drive the
successor's close bookkeeping without booting anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
