from __future__ import annotations

import dataclasses
import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixelgym.platform import mlflow_tracking
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.mlflow_tracking import (
    COMPATIBLE_SEARCH_CAPACITY,
    CompatibleSearchCapacityError,
    DatasetInputContract,
    InMemoryTracking,
    MlflowTracking,
)
from pixelgym.platform.policy import (
    RENDERER_VERSION,
    is_verified_clean_revision,
    renderer_config_sha256,
    verify_policy_manifest,
    verify_renderer_binding,
)


@pytest.fixture(autouse=True)
def _reset_compatible_search_capacity(monkeypatch) -> None:
    monkeypatch.setattr(
        mlflow_tracking,
        "_COMPATIBLE_SEARCH_SLOTS",
        threading.BoundedSemaphore(COMPATIBLE_SEARCH_CAPACITY),
    )
    monkeypatch.setattr(mlflow_tracking, "_compatible_search_occupancy", 0)


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


def _identity_digest(manifest) -> str:
    return sha256_bytes(canonical_json_bytes(manifest.identity_dict()))


def _prompt_text_and_digest_mutation(base):
    text = "a completely different packaged prompt template"
    return dataclasses.replace(
        base, prompt_template_text=text, prompt_sha256=sha256_bytes(text.encode("utf-8"))
    )


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("schema_version", lambda base: dataclasses.replace(base, schema_version="other-schema-version")),
        ("provider", lambda base: dataclasses.replace(base, provider="other-provider")),
        ("model", lambda base: dataclasses.replace(base, model="other-model")),
        ("model_alias_disclosure", lambda base: dataclasses.replace(base, model_alias_disclosure="alias")),
        ("prompt_name", lambda base: dataclasses.replace(base, prompt_name="other-prompt-name")),
        ("prompt_version", lambda base: dataclasses.replace(base, prompt_version=base.prompt_version + 1)),
        ("condition", lambda base: dataclasses.replace(base, condition="marks")),
        ("parameters", lambda base: dataclasses.replace(base, parameters={**base.parameters, "extra": True})),
        ("parser_version", lambda base: dataclasses.replace(base, parser_version="other-parser-version")),
        ("scorer_version", lambda base: dataclasses.replace(base, scorer_version="other-scorer-version")),
        ("overlay_version", lambda base: dataclasses.replace(base, overlay_version="other-overlay-version")),
        ("target_semantics", lambda base: dataclasses.replace(base, target_semantics="other-target-semantics")),
        ("code_revision", lambda base: dataclasses.replace(base, code_revision="b" * 40)),
        ("code_state", lambda base: dataclasses.replace(base, code_state="dirty")),
        ("source_tree_sha256", lambda base: dataclasses.replace(base, source_tree_sha256="c" * 64)),
        ("source_provenance_verified", lambda base: dataclasses.replace(base, source_provenance_verified=False)),
        ("dependency_lock_sha256", lambda base: dataclasses.replace(base, dependency_lock_sha256="d" * 64)),
        (
            "source_provenance_failure_reason",
            lambda base: dataclasses.replace(base, source_provenance_failure_reason="test-failure-reason"),
        ),
        ("renderer_version", lambda base: dataclasses.replace(base, renderer_version="other-renderer-version")),
        ("renderer_sha256", lambda base: dataclasses.replace(base, renderer_sha256="e" * 64)),
        ("prompt_template_text", _prompt_text_and_digest_mutation),
    ],
)
def test_every_identity_component_mutation_changes_policy_id(policy_factory, field, mutate) -> None:
    """Table-driven mutation matrix: every field in identity_dict() must be identity-bound.

    Mutating exactly one component -- including the renderer version, renderer digest,
    and the packaged prompt bytes -- must change the recomputed digest that becomes
    policy_id. A field silently excluded from identity_dict() would let a candidate that
    differs in that field alone masquerade as byte-identical to another.
    """
    base = policy_factory()
    mutated = mutate(base)
    assert _identity_digest(mutated) != _identity_digest(base), field


def test_policy_package_build_is_byte_identical_across_two_builds(policy_factory) -> None:
    first = policy_factory()
    second = policy_factory()
    assert first.policy_id == second.policy_id
    assert canonical_json_bytes(first.to_dict()) == canonical_json_bytes(second.to_dict())


def test_renderer_config_digest_matches_the_committed_schema_constant(repository_root: Path) -> None:
    """Guard against schema/code drift: the schema's hardcoded renderer_sha256 const must
    track whatever the running renderer code actually computes, or registration would
    either wrongly accept a stale renderer or wrongly reject the current one."""
    schema = json.loads((repository_root / "config/platform-policy.schema.json").read_text())
    v2_branch = next(
        clause["then"]
        for clause in schema["allOf"]
        if clause.get("if", {}).get("properties", {}).get("schema_version", {}).get("const")
        == "pixelgym-grounding-policy-v2"
    )
    assert v2_branch["properties"]["renderer_version"]["const"] == RENDERER_VERSION
    assert v2_branch["properties"]["renderer_sha256"]["const"] == renderer_config_sha256()


def test_verify_renderer_binding_fails_closed_on_missing_mismatched_or_corrupt_renderer(
    policy_factory,
) -> None:
    base = policy_factory()
    verify_renderer_binding(base)

    legacy = dataclasses.replace(
        base, renderer_version=None, renderer_sha256=None, prompt_template_text=None
    )
    with pytest.raises(ValueError, match="missing packaged renderer identity"):
        verify_renderer_binding(legacy)

    with pytest.raises(ValueError, match="unsupported renderer version"):
        verify_renderer_binding(dataclasses.replace(base, renderer_version="some-other-renderer-v1"))

    with pytest.raises(ValueError, match="renderer implementation digest mismatch"):
        verify_renderer_binding(dataclasses.replace(base, renderer_sha256="0" * 64))

    # Simulate a stored row where prompt_sha256 and prompt_template_text were corrupted
    # independently, bypassing PolicyManifest.__post_init__'s own consistency check.
    corrupt_digest = dataclasses.replace(base)
    object.__setattr__(corrupt_digest, "prompt_sha256", "1" * 64)
    with pytest.raises(ValueError, match="packaged prompt digest does not match"):
        verify_renderer_binding(corrupt_digest)


def test_policy_manifest_rejects_partial_renderer_identity_at_construction(policy_factory) -> None:
    base = policy_factory()
    with pytest.raises(ValueError, match="all present or all absent"):
        dataclasses.replace(base, renderer_version=None)
    with pytest.raises(ValueError, match="all present or all absent"):
        dataclasses.replace(base, prompt_template_text=None)


def test_policy_manifest_rejects_prompt_digest_bytes_mismatch_at_construction(policy_factory) -> None:
    base = policy_factory()
    with pytest.raises(ValueError, match="packaged prompt digest does not match"):
        dataclasses.replace(base, prompt_template_text="a different prompt template")


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
        thread
        for thread in threading.enumerate()
        if thread.name == "mlflow-compatible-run-search"
    }

    class BlockingClient:
        def __init__(self) -> None:
            self.calls = 0

        def search_runs(self, *_args, **_kwargs):
            with calls_lock:
                self.calls += 1
            started.wait(timeout=10)
            assert release.wait(timeout=10)
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
            caplog.at_level(logging.WARNING, logger="pixelgym.platform.mlflow_tracking"),
            ThreadPoolExecutor(max_workers=COMPATIBLE_SEARCH_CAPACITY) as executor,
        ):
            requests = [
                executor.submit(search_until_request_timeout)
                for _ in range(COMPATIBLE_SEARCH_CAPACITY)
            ]
            started.wait(timeout=10)
            for request in requests:
                request.result(timeout=10)

            worker_threads = [
                thread
                for thread in threading.enumerate()
                if thread.name == "mlflow-compatible-run-search"
                and thread not in baseline_threads
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
            threads_after_rejection = [
                thread
                for thread in threading.enumerate()
                if thread.name == "mlflow-compatible-run-search"
                and thread not in baseline_threads
            ]
            assert len(threads_after_rejection) == COMPATIBLE_SEARCH_CAPACITY
    finally:
        release.set()
        for thread in worker_threads:
            thread.join(timeout=10)

    current_threads = {
        thread
        for thread in threading.enumerate()
        if thread.name == "mlflow-compatible-run-search"
    }
    assert current_threads == baseline_threads
    capacity_records = [
        record
        for record in caplog.records
        if record.message.startswith("compatible-run search capacity")
    ]
    assert [record.message for record in capacity_records] == [
        "compatible-run search capacity rejected"
    ]
    assert all(
        record.compatible_search_capacity == COMPATIBLE_SEARCH_CAPACITY
        for record in capacity_records
    )
    assert capacity_records[-1].compatible_search_occupancy == COMPATIBLE_SEARCH_CAPACITY
