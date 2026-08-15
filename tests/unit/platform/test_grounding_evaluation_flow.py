from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from flows import grounding_evaluation_flow as flow_module
from flows.grounding_evaluation_flow import GroundingEvaluationFlow
from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.dependency_lock import dependency_lock_sha256
from pixelgym.platform.evaluation import (
    EvaluationRunner,
    PlatformProviderResponse,
    ScriptedReplayProvider,
)
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.mlflow_tracking import InMemoryTracking
from pixelgym.platform.source_provenance import (
    SOURCE_PROVENANCE_SCHEMA_VERSION,
    SourceProvenance,
    source_tree_sha256,
)


class FlowHarness:
    """Minimal Metaflow task object that executes the production step bodies directly."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)
        self.transition: tuple[str, dict[str, Any]] | None = None

    def __getattr__(self, name: str) -> Any:
        method = getattr(GroundingEvaluationFlow, name)
        return method.__get__(self, FlowHarness)

    def next(self, destination: Any, **options: Any) -> None:
        self.transition = (destination.__name__, options)


def _clone(flow: FlowHarness, **updates: Any) -> FlowHarness:
    values = {key: value for key, value in vars(flow).items() if key != "transition"}
    values.update(updates)
    return FlowHarness(**values)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    repository_root: Path,
    tmp_path: Path,
) -> tuple[LocalImmutableStore, InMemoryTracking, ScriptedReplayProvider, ControlStore, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = LocalImmutableStore(tmp_path / "immutable")
    tracking = InMemoryTracking()
    provider = ScriptedReplayProvider(
        repository_root / "artifacts/grounding-predictions.jsonl",
        variant="revised",
    )
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    submission_id = control.submit(
        {
            "dataset": "day3-frozen-v1",
            "prompt_version": "2",
            "model": "day3-replay-revised-v2",
            "condition": "raw",
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
        }
    )
    monkeypatch.setattr(flow_module, "_root", lambda: repository_root)
    monkeypatch.setattr(flow_module, "_store", lambda: store)
    monkeypatch.setattr(flow_module, "_tracking", lambda: tracking)
    monkeypatch.setattr(flow_module, "_provider", lambda flow: provider)
    monkeypatch.setattr(flow_module, "_control", lambda: control)
    monkeypatch.setattr(
        flow_module,
        "current",
        SimpleNamespace(flow_name="GroundingEvaluationFlow", run_id="fixture-run"),
    )
    provenance_path = tmp_path / "source-provenance.json"
    provenance_path.write_text(
        json.dumps(
            SourceProvenance(
                SOURCE_PROVENANCE_SCHEMA_VERSION,
                "a" * 40,
                source_tree_sha256(repository_root),
                "clean",
                "git-build-inputs-v1",
            ).to_dict()
        )
    )
    monkeypatch.setenv("PIXELGYM_SOURCE_PROVENANCE_PATH", str(provenance_path))
    return store, tracking, provider, control, submission_id


def _new_flow(submission_id: str) -> FlowHarness:
    return FlowHarness(
        submission_id=submission_id,
        prompt_version=2,
        model="day3-replay-revised-v2",
        maximum_calls=100,
        shard_size=25,
    )


def test_start_allows_only_frozen_scripted_pairings() -> None:
    rollback_seed = FlowHarness(
        submission_id="submission-seed",
        prompt_version=2,
        model="day3-replay-revised-rollback-seed-v1",
        maximum_calls=100,
        shard_size=25,
    )
    GroundingEvaluationFlow.start(rollback_seed)
    assert rollback_seed.transition == ("validate_and_freeze_inputs", {})

    unknown = _new_flow("submission-unknown")
    unknown.model = "unreviewed-model"
    with pytest.raises(ValueError, match="outside the scripted allowlist"):
        GroundingEvaluationFlow.start(unknown)


def test_hand_entered_revision_without_packaged_provenance_cannot_claim_clean(
    monkeypatch: pytest.MonkeyPatch, repository_root: Path, tmp_path: Path
) -> None:
    _store, _tracking, _provider, _control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )
    monkeypatch.delenv("PIXELGYM_SOURCE_PROVENANCE_PATH")
    monkeypatch.setenv("PIXELGYM_CODE_REVISION", "f" * 40)
    flow = _new_flow(submission_id)
    GroundingEvaluationFlow.validate_and_freeze_inputs(flow)
    assert flow.policy["code_revision"] == "unverifiable"
    assert flow.policy["code_state"] == "unverifiable"
    assert not flow.policy["source_provenance_verified"]
    assert flow.policy["dependency_lock_sha256"] == dependency_lock_sha256(repository_root)


def _run_flow(
    submission_id: str,
    *,
    reverse_branches: bool = False,
    stop_after: str | None = None,
) -> FlowHarness:
    flow = _new_flow(submission_id)
    GroundingEvaluationFlow.start(flow)
    GroundingEvaluationFlow.validate_and_freeze_inputs(flow)
    GroundingEvaluationFlow.create_or_recover_mlflow_run(flow)
    if stop_after == "create_or_recover_mlflow_run":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.build_shards(flow)
    if len(flow.shards) != 4 or any(len(shard["example_ids"]) != 25 for shard in flow.shards):
        raise AssertionError("flow did not build four bounded deterministic shards")
    if stop_after == "build_shards":
        raise RuntimeError("fixture interruption")

    branches: list[FlowHarness] = []
    for index, shard in enumerate(flow.shards):
        branch = _clone(flow, input=shard)
        GroundingEvaluationFlow.evaluate_shard(branch)
        branches.append(branch)
        if stop_after == "first_evaluate_shard" and index == 0:
            raise RuntimeError("fixture interruption")
    if reverse_branches:
        branches.reverse()
    joined = _new_flow(submission_id)
    GroundingEvaluationFlow.join_responses(joined, branches)
    if stop_after == "join_responses":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.verify_raw_artifacts(joined)
    if stop_after == "verify_raw_artifacts":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.parse_and_score(joined)
    if stop_after == "parse_and_score":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.aggregate_metrics(joined)
    if stop_after == "aggregate_metrics":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.evaluate_gates(joined)
    assert joined.transition == ("finalize_mlflow_run", {})
    if stop_after == "evaluate_gates":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.finalize_mlflow_run(joined)
    assert joined.transition == ("register_candidate", {})
    if stop_after == "finalize_mlflow_run":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.register_candidate(joined)
    if stop_after == "register_candidate":
        raise RuntimeError("fixture interruption")
    GroundingEvaluationFlow.end(joined)
    return joined


def _final_bytes(store: LocalImmutableStore, submission_id: str) -> tuple[bytes, bytes]:
    predictions = store.get_reference(f"runs/{submission_id}/predictions.jsonl")
    gates = store.get_reference(f"runs/{submission_id}/gate-report.json")
    assert predictions is not None and gates is not None
    return store.get_verified(predictions), store.get_verified(gates)


@pytest.fixture(scope="module")
def uninterrupted_flow_bytes(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, bytes]:
    monkeypatch = pytest.MonkeyPatch()
    repository_root = Path(__file__).parents[3]
    temporary_root = tmp_path_factory.mktemp("uninterrupted-flow")
    try:
        store, _, _, _, submission_id = _configure(
            monkeypatch, repository_root, temporary_root
        )
        _run_flow(submission_id)
        return _final_bytes(store, submission_id)
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize(
    "boundary",
    [
        "create_or_recover_mlflow_run",
        "build_shards",
        "first_evaluate_shard",
        "join_responses",
        "verify_raw_artifacts",
        "parse_and_score",
        "aggregate_metrics",
        "evaluate_gates",
        "register_candidate",
        "finalize_mlflow_run",
    ],
)
def test_flow_resumes_at_each_side_effect_boundary_without_duplicate_calls(
    boundary: str,
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uninterrupted_flow_bytes: tuple[bytes, bytes],
) -> None:
    store, tracking, provider, control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )
    with pytest.raises(RuntimeError, match="fixture interruption"):
        _run_flow(submission_id, stop_after=boundary)
    completed = _run_flow(submission_id, reverse_branches=True)

    assert completed.summary["scored_count"] == 100
    assert completed.summary["unique_record_count"] == 100
    assert len(provider.call_ids) == 100
    assert len(set(provider.call_ids)) == 100
    assert tracking.runs[completed.mlflow_run_id].status == "FINISHED"
    assert control.list_submissions()[0]["status"] == "Complete"
    final_bytes = _final_bytes(store, submission_id)
    predictions, _ = final_bytes
    assert len(predictions.splitlines()) == 100
    assert final_bytes == uninterrupted_flow_bytes


def test_parallel_shard_order_does_not_change_canonical_output_bytes(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_a, _, provider_a, _, submission_a = _configure(
        monkeypatch, repository_root, tmp_path / "a"
    )
    _run_flow(submission_a)
    bytes_a = _final_bytes(store_a, submission_a)

    store_b, _, provider_b, _, submission_b = _configure(
        monkeypatch, repository_root, tmp_path / "b"
    )
    _run_flow(submission_b, reverse_branches=True)
    bytes_b = _final_bytes(store_b, submission_b)

    assert bytes_a == bytes_b
    assert len(provider_a.call_ids) == len(provider_b.call_ids) == 100


def test_flow_rejects_invalid_caps_before_provider_execution(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, provider, _, submission_id = _configure(monkeypatch, repository_root, tmp_path)
    flow = _new_flow(submission_id)
    flow.maximum_calls = 99
    with pytest.raises(ValueError, match="exactly 100"):
        GroundingEvaluationFlow.start(flow)
    flow.maximum_calls = 100
    flow.shard_size = 0
    with pytest.raises(ValueError, match="shard size"):
        GroundingEvaluationFlow.start(flow)
    assert provider.call_ids == []


class FixedResponseProvider:
    name = "scripted-demo"
    model = "day3-replay-revised-v2"
    synthetic = True

    def __init__(self, response: PlatformProviderResponse) -> None:
        self.response = response
        self.call_ids: list[str] = []

    def invoke(self, **request: Any) -> PlatformProviderResponse:
        self.call_ids.append(request["request_id"])
        return self.response


@pytest.mark.parametrize(
    ("response", "invalid_count", "request_failure_count"),
    [
        (PlatformProviderResponse("not-json", 25.0, {}, 0.0), 100, 0),
        (
            PlatformProviderResponse(None, None, None, None, "provider failed before receipt"),
            0,
            100,
        ),
    ],
)
def test_flow_records_unparseable_and_request_failures_without_hidden_retries(
    response: PlatformProviderResponse,
    invalid_count: int,
    request_failure_count: int,
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _, submission_id = _configure(monkeypatch, repository_root, tmp_path)
    provider = FixedResponseProvider(response)
    monkeypatch.setattr(flow_module, "_provider", lambda flow: provider)

    completed = _run_flow(submission_id)
    _run_flow(submission_id, reverse_branches=True)

    assert completed.summary["invalid_count"] == invalid_count
    assert completed.summary["request_failure_count"] == request_failure_count
    assert len(provider.call_ids) == 100
    predictions, _ = _final_bytes(store, submission_id)
    assert len(predictions.splitlines()) == 100


def test_flow_failure_finalizes_partial_run_and_never_registers_candidate(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tracking, provider, control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )

    def fail_parse(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("cancelled before offline parse")

    monkeypatch.setattr(EvaluationRunner, "parse_and_score", fail_parse)
    with pytest.raises(RuntimeError, match="cancelled before offline parse"):
        _run_flow(submission_id)

    assert len(provider.call_ids) == 100
    assert next(iter(tracking.runs.values())).status == "FAILED"
    assert control.list_submissions()[0]["status"] == "Failed"
    assert control.list_candidates() == []


def test_offline_flow_steps_do_not_construct_tracking_clients(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tracking, _, _, submission_id = _configure(monkeypatch, repository_root, tmp_path)
    tracking_constructions = 0

    def count_tracking() -> InMemoryTracking:
        nonlocal tracking_constructions
        tracking_constructions += 1
        return tracking

    monkeypatch.setattr(flow_module, "_tracking", count_tracking)
    _run_flow(submission_id)

    assert tracking_constructions == 2


def test_tracking_finalization_failure_cannot_leave_an_eligible_candidate(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tracking, _, control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )

    def fail_finalization(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("tracking finalization unavailable")

    monkeypatch.setattr(EvaluationRunner, "finalize_success", fail_finalization)
    with pytest.raises(RuntimeError, match="tracking finalization unavailable"):
        _run_flow(submission_id)

    assert next(iter(tracking.runs.values())).status == "FAILED"
    assert control.list_submissions()[0]["status"] == "Failed"
    assert control.list_candidates() == []


def test_candidate_registration_failure_finalizes_terminal_state(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tracking, _, control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )

    def fail_registration(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("candidate registry unavailable")

    monkeypatch.setattr(control, "register_candidate", fail_registration)
    with pytest.raises(RuntimeError, match="candidate registry unavailable"):
        _run_flow(submission_id)

    assert next(iter(tracking.runs.values())).status == "FAILED"
    assert control.list_submissions()[0]["status"] == "Failed"
    assert control.list_candidates() == []


def test_candidate_and_submission_completion_are_atomic(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tracking, _, control, submission_id = _configure(
        monkeypatch, repository_root, tmp_path
    )
    control.connection.executescript(
        """
        CREATE TRIGGER fail_submission_completion
        BEFORE UPDATE OF status ON submissions
        WHEN NEW.status = 'Complete'
        BEGIN SELECT RAISE(ABORT, 'submission ledger unavailable'); END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="submission ledger unavailable"):
        _run_flow(submission_id)

    assert next(iter(tracking.runs.values())).status == "FAILED"
    assert control.list_submissions()[0]["status"] == "Failed"
    assert control.list_candidates() == []
