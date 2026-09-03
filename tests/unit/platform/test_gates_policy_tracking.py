from __future__ import annotations

import dataclasses
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.mlflow_tracking import (
    COMPATIBLE_SEARCH_CAPACITY,
    CompatibleSearchCapacityError,
    DatasetInputContract,
    InMemoryTracking,
    MlflowTracking,
)
from pixelgym.platform.policy import is_verified_clean_revision, verify_policy_manifest


@pytest.mark.parametrize(
    ("field", "value", "reason_fragment"),
    [
        ("accuracy", 0.799999, "accuracy"),
        ("cost_usd_per_100", 0.000001, "cost"),
        ("provider_latency_p95_ms", 100.000001, "latency"),
        ("scored_count", 99, "missing or duplicated"),
        ("dataset_fingerprint", "sha256:" + "0" * 64, "incompatible"),
        ("dirty_code", True, "dirty code"),
    ],
)
def test_each_gate_fails_independently_and_boundaries_pass(
    passing_evidence, gate_policy, field: str, value: object, reason_fragment: str
) -> None:
    _policy, summary, boundary_report = passing_evidence
    assert boundary_report.overall_passed
    changed = dataclasses.replace(summary, **{field: value})
    report = evaluate_gates(gate_policy, changed)
    assert not report.overall_passed
    assert any(reason_fragment in reason for reason in report.reasons)


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["accuracy", "cost_usd_per_100", "provider_latency_p95_ms"])
def test_missing_and_nonfinite_gate_evidence_fails_closed(
    passing_evidence, gate_policy, field: str, value: float | None
) -> None:
    _, summary, _ = passing_evidence
    report = evaluate_gates(gate_policy, dataclasses.replace(summary, **{field: value}))
    assert not report.overall_passed


def test_unpriced_or_insufficient_latency_counts_fail_closed(passing_evidence, gate_policy) -> None:
    _, summary, _ = passing_evidence
    unpriced = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, priced_call_count=99, unpriced_call_count=1),
    )
    insufficient = evaluate_gates(
        gate_policy, dataclasses.replace(summary, latency_measured_count=99)
    )
    assert not unpriced.cost_usd_per_100.passed
    assert not insufficient.provider_latency_p95_ms.passed


def test_accuracy_and_measurement_counts_must_be_internally_consistent(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    inconsistent_accuracy = evaluate_gates(
        gate_policy, dataclasses.replace(summary, accuracy=0.9)
    )
    extra_latency = evaluate_gates(
        gate_policy, dataclasses.replace(summary, latency_measured_count=101)
    )
    missing_price_record = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, priced_call_count=99, unpriced_call_count=0),
    )
    assert not inconsistent_accuracy.accuracy.passed
    assert not extra_latency.provider_latency_p95_ms.passed
    assert not missing_price_record.cost_usd_per_100.passed


def test_any_policy_component_changes_policy_id(policy_factory) -> None:
    base = policy_factory()
    changed = policy_factory(model="another-exact-model")
    assert base.policy_id != changed.policy_id
    verify_policy_manifest(base)
    with pytest.raises(ValueError, match="digest"):
        verify_policy_manifest(dataclasses.replace(base, model="tampered"))


@pytest.mark.parametrize(
    ("revision", "expected"),
    [("a" * 40, True), ("ABCDEF" * 7, False), ("clean-main", False), ("unknown-dirty", False)],
)
def test_only_exact_lowercase_git_commit_has_valid_format(
    revision: str, expected: bool
) -> None:
    assert is_verified_clean_revision(revision) is expected


def test_unverifiable_revision_fails_the_clean_code_gate(
    passing_evidence, gate_policy
) -> None:
    policy, summary, _ = passing_evidence
    assert is_verified_clean_revision(policy.code_revision)
    report = evaluate_gates(
        gate_policy,
        dataclasses.replace(summary, dirty_code=False, code_state="clean", code_provenance_verified=False),
    )
    assert not report.code_revision_passed


def test_gate_report_is_byte_stable(passing_evidence, gate_policy) -> None:
    _, summary, first = passing_evidence
    second = evaluate_gates(gate_policy, summary)
    assert first.to_dict() == second.to_dict()


def test_confidence_bound_can_pass_with_complete_auditable_evidence(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    policy = dataclasses.replace(
        gate_policy, confidence_bound_required=True, minimum_accuracy=0.70
    )

    report = evaluate_gates(policy, summary)

    assert report.overall_passed
    assert report.confidence_bound is not None
    assert report.confidence_bound.passed
    assert report.confidence_bound.method == "wilson-score-one-sided-v1"
    assert report.confidence_bound.confidence_level == 0.95
    assert report.confidence_bound.success_count == 80
    assert report.confidence_bound.sample_count == 100
    assert report.confidence_bound.observed == pytest.approx(0.7267, abs=0.0001)


def test_confidence_bound_fails_when_point_accuracy_passes_but_bound_does_not(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    policy = dataclasses.replace(gate_policy, confidence_bound_required=True)

    report = evaluate_gates(policy, summary)

    assert not report.overall_passed
    assert report.accuracy.passed
    assert report.confidence_bound is not None
    assert not report.confidence_bound.passed
    assert any("Wilson" in reason for reason in report.reasons)


def test_confidence_bound_threshold_boundary_is_inclusive(passing_evidence, gate_policy) -> None:
    _, summary, _ = passing_evidence
    first = evaluate_gates(
        dataclasses.replace(gate_policy, confidence_bound_required=True, minimum_accuracy=0.0),
        summary,
    )
    assert first.confidence_bound is not None
    policy = dataclasses.replace(
        gate_policy,
        confidence_bound_required=True,
        minimum_accuracy=first.confidence_bound.observed,
    )

    report = evaluate_gates(policy, summary)

    assert report.confidence_bound is not None
    assert report.confidence_bound.passed
    assert report.overall_passed


@pytest.mark.parametrize(
    "changes",
    [
        {"scored_count": 0, "correct_count": 0, "accuracy": None},
        {"expected_count": None, "accuracy": None},
        {"scored_count": "100", "accuracy": None},
        {"correct_count": 101},
        {"invalid_count": -1},
        {"request_failure_count": 21},
        {"invalid_count": 20, "request_failure_count": 1, "correct_count": 80},
        {"invalid_count": 21, "correct_count": 80, "accuracy": 0.8},
    ],
)
def test_confidence_bound_fails_closed_for_missing_or_inconsistent_counts(
    passing_evidence, gate_policy, changes: dict[str, object]
) -> None:
    _, summary, _ = passing_evidence
    policy = dataclasses.replace(gate_policy, confidence_bound_required=True, minimum_accuracy=0.7)

    report = evaluate_gates(policy, dataclasses.replace(summary, **changes))

    assert not report.overall_passed
    assert report.confidence_bound is not None
    assert not report.confidence_bound.passed


def test_confidence_bound_retains_invalid_and_failed_requests_as_incorrect(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    policy = dataclasses.replace(gate_policy, confidence_bound_required=True, minimum_accuracy=0.70)
    with_invalid = dataclasses.replace(
        summary,
        correct_count=80,
        invalid_count=10,
        request_failure_count=10,
        accuracy=0.8,
    )

    report = evaluate_gates(policy, with_invalid)

    assert report.confidence_bound is not None
    assert report.confidence_bound.sample_count == 100
    assert report.confidence_bound.success_count == 80
    assert report.confidence_bound.observed == pytest.approx(0.7267, abs=0.0001)
    assert report.overall_passed


def test_confidence_bound_report_round_trips_and_old_reports_remain_readable(
    passing_evidence, gate_policy
) -> None:
    _, summary, _ = passing_evidence
    policy = dataclasses.replace(gate_policy, confidence_bound_required=True, minimum_accuracy=0.70)
    report = evaluate_gates(policy, summary)

    assert type(report).from_dict(report.to_dict()) == report
    old_shape = report.to_dict()
    old_shape.pop("confidence_bound")
    old_shape["reasons"] = []
    decoded = type(report).from_dict(old_shape)
    assert decoded.confidence_bound is None


@pytest.mark.parametrize("confidence_level", [0.0, 1.0, math.nan, math.inf])
def test_confidence_level_must_be_a_finite_open_probability(gate_policy, confidence_level: float) -> None:
    with pytest.raises(ValueError, match="confidence level"):
        dataclasses.replace(gate_policy, confidence_level=confidence_level)


def test_tracking_contract_is_idempotent_and_params_are_immutable(
    passing_evidence, policy_factory
) -> None:
    policy, summary, _report = passing_evidence
    tracking = InMemoryTracking()
    params = {
        "dataset_name": "pixelgym-grounding-day3-frozen",
        "dataset_fingerprint": summary.dataset_fingerprint,
        "mlflow_dataset_digest": summary.dataset_fingerprint.removeprefix("sha256:")[:32],
        "dataset_manifest_uri": "s3://immutable/dataset.json",
        "dataset_manifest_version": "version-1",
        "dataset_schema": "pixelgym-grounding-dataset-manifest-v1",
        "dataset_protocol_version": "pixelgym-grounding-v1",
        "dataset_example_count": 100,
        "prompt_name": policy.prompt_name,
        "prompt_version": policy.prompt_version,
        "prompt_sha256": policy.prompt_sha256,
        "rendered_template_schema": "pixelgym-grounding-rendered-prompt-v1",
        "provider": policy.provider,
        "model": policy.model,
        "model_alias_disclosure": policy.model_alias_disclosure,
        "endpoint_class": "local-scripted",
        "structured_output_mode": "json-schema",
        "inference_temperature": None,
        "inference_reasoning": "unsupported",
        "inference_seed": None,
        "inference_max_output": "provider-default",
        "condition": policy.condition,
        "retry_policy": {"hidden_retries": 0, "request_retry": "none"},
        "concurrency": 1,
        "parser_version": policy.parser_version,
        "scorer_version": policy.scorer_version,
        "target_semantics": policy.target_semantics,
        "price_catalog_version": "pixelgym-demo-prices-v1",
        "code_revision": policy.code_revision,
        "code_state": policy.code_state,
        "source_tree_sha256": policy.source_tree_sha256,
        "source_provenance_verified": policy.source_provenance_verified,
        "source_provenance_failure_reason": policy.source_provenance_failure_reason,
        "dependency_lock_sha256": policy.dependency_lock_sha256,
        "python_version": "3.12.0",
        "metaflow_flow_name": "GroundingEvaluationFlow",
        "metaflow_attempt": 0,
        "metaflow_resume_origin": "GroundingEvaluationFlow/1",
        "submission_id": "submission-1",
        "synthetic_provider": True,
    }
    run_id = tracking.create_or_recover_run("submission-1", params)
    assert tracking.create_or_recover_run("submission-1", params) == run_id
    dataset_input = DatasetInputContract(
        name=params["dataset_name"],
        fingerprint=summary.dataset_fingerprint,
        mlflow_digest=params["mlflow_dataset_digest"],
        manifest_uri=params["dataset_manifest_uri"],
        manifest_version=params["dataset_manifest_version"],
        schema=params["dataset_schema"],
        protocol_version=params["dataset_protocol_version"],
        example_count=100,
    )
    tracking.log_dataset_input(run_id, dataset_input)
    tracking.log_dataset_input(run_id, dataset_input)
    assert tracking.runs[run_id].dataset_inputs == [dataset_input]
    with pytest.raises(ValueError, match="immutable dataset input changed"):
        tracking.log_dataset_input(
            run_id, dataclasses.replace(dataset_input, manifest_version="version-2")
        )
    tracking.reconcile_pathspec(run_id, "GroundingEvaluationFlow/1")
    with pytest.raises(ValueError, match="changed"):
        tracking.create_or_recover_run("submission-1", {**params, "model": "different"})
    with pytest.raises(ValueError, match="unsafe"):
        tracking.create_or_recover_run("submission-' OR 1=1", params)


def test_mlflow_policy_version_scan_consumes_every_page() -> None:
    class Page(list):
        def __init__(self, items, token):
            super().__init__(items)
            self.token = token

    first = SimpleNamespace(tags={"policy_id": "policy-a"})
    second = SimpleNamespace(tags={"policy_id": "policy-b"})

    class Client:
        def __init__(self) -> None:
            self.tokens = []

        def search_model_versions(self, _filter, *, max_results, page_token):
            assert max_results == 1000
            self.tokens.append(page_token)
            return Page([first], "next") if page_token is None else Page([second], None)

    tracking = object.__new__(MlflowTracking)
    tracking.client = Client()

    assert tracking._all_policy_versions() == [first, second]
    assert tracking.client.tokens == [None, "next"]


def test_compatible_search_caps_timed_out_workers_and_recovers_threads(caplog) -> None:
    started = threading.Barrier(COMPATIBLE_SEARCH_CAPACITY + 1)
    release = threading.Event()
    calls_lock = threading.Lock()
    baseline_threads = {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == "mlflow-compatible-run-search"
    }

    class BlockingClient:
        def __init__(self) -> None:
            self.calls = 0

        def search_runs(self, *_args, **_kwargs):
            with calls_lock:
                self.calls += 1
            started.wait(timeout=2)
            assert release.wait(timeout=2)
            return []

    tracking = object.__new__(MlflowTracking)
    tracking.client = BlockingClient()
    tracking.experiment_id = "experiment-1"

    def search_until_request_timeout() -> None:
        with pytest.raises(TimeoutError, match="exceeded its deadline"):
            tracking.search_compatible_runs(
                dataset_fingerprint="sha256:" + "a" * 64,
                scorer_version="scorer-v1",
                target_semantics="target-v1",
                timeout_seconds=0.01,
            )

    worker_threads: list[threading.Thread] = []
    try:
        with (
            caplog.at_level(logging.INFO, logger="pixelgym.platform.mlflow_tracking"),
            ThreadPoolExecutor(max_workers=COMPATIBLE_SEARCH_CAPACITY) as executor,
        ):
            requests = [
                executor.submit(search_until_request_timeout)
                for _ in range(COMPATIBLE_SEARCH_CAPACITY)
            ]
            started.wait(timeout=2)
            for request in requests:
                request.result(timeout=1)

            worker_threads = [
                thread
                for thread in threading.enumerate()
                if thread.name == "mlflow-compatible-run-search"
                and thread.ident not in baseline_threads
            ]
            assert len(worker_threads) == COMPATIBLE_SEARCH_CAPACITY

            with pytest.raises(CompatibleSearchCapacityError) as rejected:
                tracking.search_compatible_runs(
                    dataset_fingerprint="sha256:" + "b" * 64,
                    scorer_version="scorer-v1",
                    target_semantics="target-v1",
                    timeout_seconds=0.01,
                )
            assert rejected.value.capacity == COMPATIBLE_SEARCH_CAPACITY
            assert rejected.value.occupancy == COMPATIBLE_SEARCH_CAPACITY
            assert tracking.client.calls == COMPATIBLE_SEARCH_CAPACITY
    finally:
        release.set()
        for thread in worker_threads:
            thread.join(timeout=1)

    current_threads = {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == "mlflow-compatible-run-search"
    }
    assert current_threads == baseline_threads
    capacity_records = [
        record
        for record in caplog.records
        if record.message.startswith("compatible-run search capacity")
    ]
    assert [record.message for record in capacity_records].count(
        "compatible-run search capacity acquired"
    ) == COMPATIBLE_SEARCH_CAPACITY
    assert [record.message for record in capacity_records].count(
        "compatible-run search capacity rejected"
    ) == 1
    assert all(
        record.compatible_search_capacity == COMPATIBLE_SEARCH_CAPACITY
        for record in capacity_records
    )
    assert capacity_records[-1].compatible_search_occupancy == COMPATIBLE_SEARCH_CAPACITY
