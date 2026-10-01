from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.platform.runtime_fixture import (
    LedgeredScriptedReplayProvider,
    provider_ledger_snapshot,
)


def test_ledgered_provider_separates_attempts_from_billable_calls(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    provider = LedgeredScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl",
        variant="revised",
        ledger_path=tmp_path / "provider.db",
    )
    example_id = next(iter(provider.responses))
    request = {
        "request_id": "sha256:" + "a" * 64,
        "example_id": example_id,
        "condition": "raw",
        "image_path": repository_root / "unused.png",
        "prompt": "unused",
        "schema": {},
    }

    first = provider.invoke(**request)
    second = provider.invoke(**request)

    assert first == second
    assert provider_ledger_snapshot(tmp_path / "provider.db") == {
        "attempts": 2,
        "unique_request_ids": 1,
        "cache_hits": 1,
        "active": 0,
        "max_active": 1,
        "billable_calls": 1,
    }


def test_concurrency_barrier_timeout_is_injected_not_read_from_environment(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A positive environment value must not override the injected parameter.
    monkeypatch.setenv("PIXELGYM_TEST_CONCURRENCY_BARRIER_TIMEOUT_SECONDS", "30")
    provider = LedgeredScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl",
        variant="revised",
        ledger_path=tmp_path / "provider.db",
        concurrency_barrier=2,
        concurrency_barrier_timeout_seconds=0,
    )
    with pytest.raises(ValueError, match="barrier timeout must be positive"):
        provider._wait_for_concurrency_barrier()


def test_concurrency_barrier_timeout_default_is_unchanged(
    repository_root: Path, tmp_path: Path
) -> None:
    provider = LedgeredScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl",
        variant="revised",
        ledger_path=tmp_path / "provider.db",
    )
    assert provider.concurrency_barrier_timeout_seconds == 10.0
