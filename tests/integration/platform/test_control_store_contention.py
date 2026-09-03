from __future__ import annotations

import multiprocessing
import queue
import time
from multiprocessing.synchronize import Barrier, Event
from pathlib import Path
from typing import Any

import pytest

from pixelgym.platform.contracts import RunSummary
from pixelgym.platform.control_store import ConflictError, ContentionError, ControlStore
from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.policy import PROMPT_NAME, build_policy_manifest, prompt_template
from pixelgym.platform.schema_validation import load_gate_policy
from pixelgym.platform.source_provenance import (
    SOURCE_PROVENANCE_SCHEMA_VERSION,
    SourceProvenance,
)

_JOIN_TIMEOUT_SECONDS = 10.0
_SYNC_TIMEOUT_SECONDS = 5.0


def _close_store(store: ControlStore | None) -> None:
    if store is not None:
        store.connection.close()


def _race_activation(
    database: str,
    candidate_id: str,
    barrier: Barrier,
    results: Any,
) -> None:
    store: ControlStore | None = None
    try:
        store = ControlStore(
            database,
            reviewer_identity="local-reviewer",
            busy_timeout_ms=1_000,
        )
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        deployment = store.activate(
            candidate_id,
            actor="local-reviewer",
            reason="racing deployment",
            action="deploy",
            expected_deployment_id=None,
            expected_generation=0,
        )
        results.put(("winner", deployment.deployment_id))
    except ConflictError as exc:
        results.put(("conflict", str(exc)))
    except Exception as exc:  # noqa: BLE001 - child must return unexpected failures.
        results.put(("unexpected", type(exc).__name__, str(exc)))
    finally:
        _close_store(store)


def _hold_uncommitted_write(
    database: str,
    barrier: Barrier,
    locked: Event,
    release: Event,
) -> None:
    store: ControlStore | None = None
    try:
        store = ControlStore(database, reviewer_identity="local-reviewer")
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        store.connection.execute("BEGIN IMMEDIATE")
        store.connection.execute("UPDATE submissions SET status = 'Running'")
        locked.set()
        if not release.wait(timeout=_SYNC_TIMEOUT_SECONDS):
            raise TimeoutError("writer release was not signaled")
        store.connection.rollback()
    finally:
        _close_store(store)


def _read_while_writer_active(
    database: str,
    barrier: Barrier,
    locked: Event,
    completed: Event,
    results: Any,
) -> None:
    store: ControlStore | None = None
    try:
        store = ControlStore(database, reviewer_identity="local-reviewer")
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        if not locked.wait(timeout=_SYNC_TIMEOUT_SECONDS):
            raise TimeoutError("writer did not acquire its transaction")
        rows = store.list_submissions()
        results.put(("read", [row["status"] for row in rows]))
    except Exception as exc:  # noqa: BLE001 - child must return unexpected failures.
        results.put(("unexpected", type(exc).__name__, str(exc)))
    finally:
        completed.set()
        _close_store(store)


def _hold_writer_lock(
    database: str,
    barrier: Barrier,
    locked: Event,
    release: Event,
) -> None:
    store: ControlStore | None = None
    try:
        store = ControlStore(database, reviewer_identity="local-reviewer")
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        store.connection.execute("BEGIN IMMEDIATE")
        locked.set()
        if not release.wait(timeout=_SYNC_TIMEOUT_SECONDS):
            raise TimeoutError("writer release was not signaled")
        store.connection.rollback()
    finally:
        _close_store(store)


def _transient_waiter(
    database: str,
    barrier: Barrier,
    locked: Event,
    attempting: Event,
    results: Any,
) -> None:
    store: ControlStore | None = None
    try:
        store = ControlStore(
            database,
            reviewer_identity="local-reviewer",
            busy_timeout_ms=1_000,
        )
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        if not locked.wait(timeout=_SYNC_TIMEOUT_SECONDS):
            raise TimeoutError("writer did not acquire its transaction")

        def trace(statement: str) -> None:
            if statement == "BEGIN IMMEDIATE":
                attempting.set()

        store.connection.set_trace_callback(trace)
        submission_id = store.submit({"model": "transient-lock"})
        results.put(("submitted", submission_id))
    except Exception as exc:  # noqa: BLE001 - child must return unexpected failures.
        results.put(("unexpected", type(exc).__name__, str(exc)))
    finally:
        _close_store(store)


def _finish_processes(processes: list[multiprocessing.Process]) -> None:
    deadline = time.monotonic() + _JOIN_TIMEOUT_SECONDS
    try:
        for process in processes:
            process.join(timeout=max(0.0, deadline - time.monotonic()))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=1.0)
    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0] * len(processes)


def _prepare_approved_candidate(database: Path) -> str:
    repository_root = Path(__file__).parents[3]
    gate_policy = load_gate_policy(repository_root)
    policy = build_policy_manifest(
        provider="scripted-demo",
        model="day3-replay-revised-v2",
        prompt_name=PROMPT_NAME,
        prompt_version=2,
        prompt=prompt_template(2),
        condition="raw",
        parameters={"deterministic": True, "hidden_retries": 0},
        parser_version="pixelgym-grounding-parser-v1",
        scorer_version=gate_policy.required_scorer_version,
        overlay_version="none-raw-coordinate-policy",
        target_semantics=gate_policy.required_target_semantics,
        source_provenance=SourceProvenance(
            SOURCE_PROVENANCE_SCHEMA_VERSION,
            "a" * 40,
            "b" * 64,
            "clean",
            "git-build-inputs-v1",
        ),
        dependency_lock_sha256=dependency_lock_sha256(repository_root),
    )
    summary = RunSummary(
        run_id="contention-run",
        dataset_fingerprint=gate_policy.required_dataset_fingerprint,
        policy_id=policy.policy_id,
        scorer_version=gate_policy.required_scorer_version,
        target_semantics=gate_policy.required_target_semantics,
        expected_count=100,
        scored_count=100,
        unique_record_count=100,
        correct_count=80,
        accuracy=0.8,
        cost_usd_per_100=0.0,
        priced_call_count=100,
        unpriced_call_count=0,
        provider_latency_p95_ms=100.0,
        latency_measured_count=100,
        code_state="clean",
        code_provenance_verified=True,
    )
    store = ControlStore(database, reviewer_identity="local-reviewer")
    try:
        store.migrate()
        candidate = store.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=evaluate_gates(gate_policy, summary),
            artifacts=[],
        )
        store.approve(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="contention fixture",
            gate_report_sha256=candidate.gate_report_sha256,
        )
        return candidate.candidate_id
    finally:
        store.connection.close()


def _get_result(results: Any) -> tuple[Any, ...]:
    try:
        return results.get(timeout=_SYNC_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise AssertionError("child process did not publish a result") from exc


def test_two_processes_racing_activation_yield_one_winner_and_one_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.db"
    candidate_id = _prepare_approved_candidate(database)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_activation,
            args=(str(database), candidate_id, barrier, results),
        )
        for _ in range(2)
    ]
    started: list[multiprocessing.Process] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        outcomes = [_get_result(results), _get_result(results)]
    finally:
        _finish_processes(started)

    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "winner"]
    assert all(outcome[0] != "unexpected" for outcome in outcomes)
    reopened = ControlStore(database, reviewer_identity="local-reviewer")
    try:
        active, generation = reopened.active()
        assert active is not None
        assert generation == 1
        assert len(reopened.deployment_history()) == 1
    finally:
        reopened.connection.close()


def test_reader_completes_while_other_process_holds_immediate_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.db"
    setup = ControlStore(database, reviewer_identity="local-reviewer")
    setup.migrate()
    setup.submit({"model": "visible-before-write"})
    setup.connection.close()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    locked = context.Event()
    release = context.Event()
    completed = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_hold_uncommitted_write,
            args=(str(database), barrier, locked, release),
        ),
        context.Process(
            target=_read_while_writer_active,
            args=(str(database), barrier, locked, completed, results),
        ),
    ]
    started: list[multiprocessing.Process] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        assert completed.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        assert not release.is_set()
        outcome = _get_result(results)
    finally:
        release.set()
        _finish_processes(started)

    assert outcome == ("read", ["Submitted"])


def test_transient_writer_lock_succeeds_within_busy_timeout(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    setup = ControlStore(database, reviewer_identity="local-reviewer")
    setup.migrate()
    setup.connection.close()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    locked = context.Event()
    attempting = context.Event()
    release = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_hold_writer_lock,
            args=(str(database), barrier, locked, release),
        ),
        context.Process(
            target=_transient_waiter,
            args=(str(database), barrier, locked, attempting, results),
        ),
    ]
    started: list[multiprocessing.Process] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        assert attempting.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        release.set()
        outcome = _get_result(results)
    finally:
        release.set()
        _finish_processes(started)

    assert outcome[0] == "submitted"


def test_over_bound_writer_lock_maps_to_stable_contention_error(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    setup = ControlStore(database, reviewer_identity="local-reviewer")
    setup.migrate()
    setup.connection.close()
    blocked = ControlStore(
        database,
        reviewer_identity="local-reviewer",
        busy_timeout_ms=200,
    )
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    locked = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_writer_lock,
        args=(str(database), barrier, locked, release),
    )
    started: list[multiprocessing.Process] = []
    try:
        process.start()
        started.append(process)
        barrier.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        assert locked.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        with pytest.raises(
            ContentionError,
            match="control database is temporarily busy; retry the request",
        ):
            blocked.submit({"model": "must-time-out"})
    finally:
        release.set()
        _finish_processes(started)

    submission_id = blocked.submit({"model": "after-lock-release"})
    assert blocked.get_submission(submission_id)["status"] == "Submitted"
    blocked.connection.close()
