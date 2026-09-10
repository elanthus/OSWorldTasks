from __future__ import annotations

import concurrent.futures
import copy
import dataclasses
import json
import threading
from pathlib import Path

import pytest

from pixelgym.platform import schema_validation
from pixelgym.platform.contracts import GateReport, PolicyManifest
from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.policy import (
    LEGACY_POLICY_SCHEMA_VERSION,
    PROMPT_NAME,
    verify_policy_manifest,
)
from pixelgym.platform.schema_validation import (
    CONTRACT_SCHEMA_FILES,
    ContractValidationError,
    PlatformSchemas,
    load_gate_policy,
    load_policy_manifest,
    load_price_catalog,
)
from scripts.export_platform_evidence import export_evidence
from tests.unit.platform.stateful_fixtures import stateful_representatives

COMMITTED_EXPORTS = {
    "demo-run-manifests.jsonl": "run_manifest",
    "demo-gate-reports.jsonl": "gate_report",
    "demo-approval-events.jsonl": "approval",
    "demo-deployment-events.jsonl": "deployment",
    "demo-audit-events.jsonl": "audit_event",
}
WRONG_TYPE_FIELDS = {
    "run_manifest": "submission_id",
    "gate_policy": "schema_version",
    "gate_report": "run_id",
    "policy_package": "provider",
    "raw_response": "example_id",
    "approval": "actor",
    "deployment": "generation",
    "audit_event": "event_type",
    "stateful_policy_package": "provider",
    "serving_create_request": "task_instruction",
    "serving_create_response": "max_steps",
    "serving_act_request": "screenshot",
    "serving_act_response": "attempt_count",
    "serving_close_request": "final_screenshot",
    "serving_close_response": "steps",
    "serving_episode_status": "open",
    "episode_session_state": "revision",
    "episode_opened_record": "max_steps",
    "episode_step_record": "step_index",
    "episode_closed_record": "steps",
}


def test_validator_cache_initialization_is_thread_safe(repository_root: Path, monkeypatch) -> None:
    schemas = PlatformSchemas(repository_root)
    original_validator = schema_validation.Draft202012Validator
    constructor_entered = threading.Event()
    release_constructor = threading.Event()
    constructor_calls = 0
    count_lock = threading.Lock()

    def blocking_constructor(*args, **kwargs):
        nonlocal constructor_calls
        with count_lock:
            constructor_calls += 1
        constructor_entered.set()
        assert release_constructor.wait(timeout=1)
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(schema_validation, "Draft202012Validator", blocking_constructor)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(schemas._validator, "raw_response") for _ in range(8)]
        assert constructor_entered.wait(timeout=1)
        release_constructor.set()
        validators = [future.result(timeout=1) for future in futures]

    assert constructor_calls == 1
    assert all(validator is validators[0] for validator in validators)


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _raw_response(policy_id: str, dataset_fingerprint: str) -> dict[str, object]:
    digest = "1" * 64
    return {
        "schema_version": "pixelgym-raw-response-v1",
        "request_id": "sha256:" + digest,
        "example_id": "vendor-form-0001",
        "dataset_fingerprint": dataset_fingerprint,
        "policy_id": policy_id,
        "request_sha256": digest,
        "started_at_utc": None,
        "started_at_missing_reason": "provider omitted request start",
        "latency_ms": 25.0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "usage_missing_reason": None,
        "price_catalog_version": "pixelgym-demo-prices-v1",
        "cost_usd": 0.0,
        "cost_missing_reason": None,
        "response_media_type": "application/json",
        "response_sha256": "2" * 64,
        "raw_response": "{}",
        "request_status": "responded",
        "request_failure": None,
    }


def _representatives(
    repository_root: Path,
    passing_evidence: tuple[object, object, GateReport],
) -> dict[str, dict[str, object]]:
    policy, _summary, report = passing_evidence
    assert isinstance(policy, PolicyManifest)
    platform = repository_root / "artifacts/platform"
    return {
        "run_manifest": _jsonl(platform / "demo-run-manifests.jsonl")[0],
        "gate_policy": load_gate_policy(repository_root).to_dict(),
        "gate_report": report.to_dict(),
        "policy_package": policy.to_dict(),
        "raw_response": _raw_response(policy.policy_id, report.dataset_fingerprint),
        "approval": _jsonl(platform / "demo-approval-events.jsonl")[0],
        "deployment": _jsonl(platform / "demo-deployment-events.jsonl")[0],
        "audit_event": _jsonl(platform / "demo-audit-events.jsonl")[0],
        **stateful_representatives(),
    }


def test_registry_inventory_matches_every_d41_contract(repository_root: Path) -> None:
    schemas = PlatformSchemas(repository_root)

    assert set(schemas.versions) == set(CONTRACT_SCHEMA_FILES)
    assert schemas.versions == {
        "run_manifest": "pixelgym-platform-run-manifest-v1",
        "gate_policy": "pixelgym-promotion-gate-policy-v1",
        "gate_report": "pixelgym-promotion-gate-report-v1",
        "policy_package": "pixelgym-grounding-policy-v2",
        "raw_response": "pixelgym-raw-response-v1",
        "approval": "pixelgym-policy-approval-v1",
        "deployment": "pixelgym-policy-deployment-v1",
        "audit_event": "pixelgym-platform-audit-event-v1",
        "stateful_policy_package": "pixelgym-stateful-policy-package-v1",
        "serving_create_request": "pixelgym-serving-session-v2",
        "serving_create_response": "pixelgym-serving-session-v2",
        "serving_act_request": "pixelgym-serving-session-v2",
        "serving_act_response": "pixelgym-serving-session-v2",
        "serving_close_request": "pixelgym-serving-session-v2",
        "serving_close_response": "pixelgym-serving-session-v2",
        "serving_episode_status": "pixelgym-serving-session-v2",
        "episode_session_state": "pixelgym-serving-session-v2",
        "episode_opened_record": "pixelgym-serving-episode-record-v1",
        "episode_step_record": "pixelgym-serving-episode-record-v1",
        "episode_closed_record": "pixelgym-serving-episode-record-v1",
    }


def test_packaged_schemas_match_authoritative_config(repository_root: Path) -> None:
    config = repository_root / "config"
    packaged = repository_root / "pixelgym/platform/schemas"
    config_names = {path.name for path in config.glob("*.schema.json")}
    packaged_names = {path.name for path in packaged.glob("*.schema.json")}

    assert packaged_names == config_names
    for config_path in sorted(config.glob("*.schema.json")):
        assert (packaged / config_path.name).read_bytes() == config_path.read_bytes()


def test_committed_configuration_and_exports_validate(repository_root: Path) -> None:
    schemas = PlatformSchemas(repository_root)
    gate_policy = load_gate_policy(repository_root)
    price_catalog = load_price_catalog(repository_root)

    assert gate_policy.confidence_level == 0.95
    assert price_catalog["catalog_version"] == "pixelgym-demo-prices-v1"
    for filename, contract in COMMITTED_EXPORTS.items():
        for value in _jsonl(repository_root / "artifacts/platform" / filename):
            schemas.validate(contract, value)


def test_representative_python_contracts_validate(repository_root: Path, passing_evidence) -> None:
    schemas = PlatformSchemas(repository_root)

    for contract, value in _representatives(repository_root, passing_evidence).items():
        schemas.validate(contract, value)


@pytest.mark.parametrize("contract", sorted(CONTRACT_SCHEMA_FILES))
@pytest.mark.parametrize("fault", ["unknown", "missing", "wrong_type"])
def test_unknown_missing_and_wrongly_typed_fields_fail_for_every_contract(
    repository_root: Path,
    passing_evidence,
    contract: str,
    fault: str,
) -> None:
    schemas = PlatformSchemas(repository_root)
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)[contract])
    field = WRONG_TYPE_FIELDS[contract]
    if fault == "unknown":
        value["unexpected_field"] = True
    elif fault == "missing":
        value.pop(field)
    else:
        value[field] = []

    with pytest.raises(ContractValidationError, match=contract):
        schemas.validate(contract, value)


def test_config_loaders_reject_invalid_data_before_runtime_use(
    repository_root: Path, tmp_path: Path
) -> None:
    invalid_gate = json.loads((repository_root / "config/promotion-gates.demo-v1.json").read_text())
    invalid_gate["provider_api_key"] = "must-not-be-accepted"
    gate_path = tmp_path / "gates.json"
    gate_path.write_text(json.dumps(invalid_gate), encoding="utf-8")

    invalid_prices = load_price_catalog(repository_root)
    invalid_prices["entries"].append(copy.deepcopy(invalid_prices["entries"][0]))
    prices_path = tmp_path / "prices.json"
    prices_path.write_text(json.dumps(invalid_prices), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="gate_policy"):
        load_gate_policy(repository_root, gate_path)
    with pytest.raises(ContractValidationError, match="duplicate"):
        load_price_catalog(repository_root, prices_path)


def test_audit_serialized_and_parsed_details_must_match(
    repository_root: Path, passing_evidence
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["audit_event"])
    value["details"] = {"different": True}

    with pytest.raises(ContractValidationError, match="does not match"):
        PlatformSchemas(repository_root).validate("audit_event", value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("latency_ms", float("nan")),
        ("usage", {1: 1}),
        ("usage", {"input_tokens": (1, 2)}),
    ],
)
def test_non_json_values_fail_before_schema_validation(
    repository_root: Path,
    passing_evidence,
    field: str,
    invalid: object,
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["raw_response"])
    value[field] = invalid

    with pytest.raises(ContractValidationError, match="JSON"):
        PlatformSchemas(repository_root).validate("raw_response", value)


def test_date_time_format_is_enforced(repository_root: Path, passing_evidence) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["approval"])
    value["created_at_utc"] = "not-a-timestamp"

    with pytest.raises(ContractValidationError, match="date-time"):
        PlatformSchemas(repository_root).validate("approval", value)


@pytest.mark.parametrize("field", ["gate_policy_version", "run_id"])
def test_gate_report_rejects_empty_provenance_identifiers(
    repository_root: Path,
    passing_evidence,
    field: str,
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["gate_report"])
    value[field] = ""

    with pytest.raises(ContractValidationError, match="non-empty"):
        PlatformSchemas(repository_root).validate("gate_report", value)


@pytest.mark.parametrize(
    "path",
    [
        ("accuracy", "passed"),
        ("cost_usd_per_100", "passed"),
        ("provider_latency_p95_ms", "passed"),
        ("completeness", "passed"),
        ("compatibility_passed",),
        ("code_revision_passed",),
        ("confidence_bound", "passed"),
    ],
)
def test_gate_report_rejects_passing_overall_with_failed_component(
    repository_root: Path,
    passing_evidence,
    path: tuple[str, ...],
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["gate_report"])
    if path[0] == "confidence_bound":
        value["confidence_bound"] = {
            "observed": 0.75,
            "threshold": 0.8,
            "passed": False,
            "method": "wilson-score-one-sided-v1",
            "confidence_level": 0.95,
            "success_count": 75,
            "sample_count": 100,
        }
    target = value
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = False

    with pytest.raises(ContractValidationError, match="gate_report"):
        PlatformSchemas(repository_root).validate("gate_report", value)

    value["overall_passed"] = False
    PlatformSchemas(repository_root).validate("gate_report", value)


def test_gate_report_rejects_passing_overall_with_blocking_reasons(
    repository_root: Path,
    passing_evidence,
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["gate_report"])
    value["reasons"] = ["blocking evidence retained"]

    with pytest.raises(ContractValidationError, match="gate_report"):
        PlatformSchemas(repository_root).validate("gate_report", value)


@pytest.mark.parametrize("field", ["dataset_fingerprint", "policy_id"])
def test_complete_run_manifest_requires_dataset_and_policy_provenance(
    repository_root: Path,
    passing_evidence,
    field: str,
) -> None:
    value = copy.deepcopy(_representatives(repository_root, passing_evidence)["run_manifest"])
    value[field] = None

    with pytest.raises(ContractValidationError, match="not of type 'string'"):
        PlatformSchemas(repository_root).validate("run_manifest", value)

    value["status"] = "Failed"
    PlatformSchemas(repository_root).validate("run_manifest", value)


def test_invalid_policy_reports_current_and_legacy_schema_failures(
    repository_root: Path,
) -> None:
    with pytest.raises(ContractValidationError) as captured:
        load_policy_manifest(PlatformSchemas(repository_root), {})

    assert "policy_package" in str(captured.value)
    assert isinstance(captured.value.__cause__, ContractValidationError)
    assert "legacy_policy_package" in str(captured.value.__cause__)


def test_v2_policy_round_trips_through_load_policy_manifest(
    repository_root: Path, passing_evidence
) -> None:
    """A current v2-schema policy package, produced by build_policy_manifest with full
    renderer identity and provenance, loads back through load_policy_manifest via the
    direct (non-legacy) path with its identity intact."""
    policy, _summary, _report = passing_evidence
    assert policy.schema_version == "pixelgym-grounding-policy-v2"

    decoded = load_policy_manifest(PlatformSchemas(repository_root), policy.to_dict())

    verify_policy_manifest(decoded)
    assert decoded.policy_id == policy.policy_id
    assert decoded.renderer_version == policy.renderer_version
    assert decoded.prompt_template_text == policy.prompt_template_text


def test_former_v1_policy_with_no_renderer_keys_remains_readable_with_its_stored_id(
    repository_root: Path,
) -> None:
    """A literal pre-renderer-identity v1 package -- the actual historical shape,
    carrying no renderer_version/renderer_sha256/prompt_template_text keys and no
    source-provenance keys at all -- must still load, unchanged, under the policy_id it
    was stored with. The legacy schema's schema_version is const-pinned to v1 (not
    widened to also accept v2), so this fixture only round-trips because it is
    genuinely v1-shaped, not because the legacy schema was loosened to let it through.
    """
    fields = {
        "schema_version": LEGACY_POLICY_SCHEMA_VERSION,
        "provider": "scripted-demo",
        "model": "day3-replay-baseline-v1",
        "model_alias_disclosure": None,
        "prompt_name": PROMPT_NAME,
        "prompt_version": 1,
        "prompt_sha256": "e" * 64,
        "condition": "raw",
        "parameters": {"deterministic": True, "hidden_retries": 0},
        "parser_version": "pixelgym-grounding-parser-v1",
        "scorer_version": "pixelgym-grounding-scorer-v1",
        "overlay_version": "none-raw-coordinate-policy",
        "target_semantics": "pixelgym-grounding-target-semantics-v1",
        "code_revision": "a" * 40,
        "dependency_lock_sha256": "b" * 64,
    }
    stored_policy_id = "sha256:" + sha256_bytes(canonical_json_bytes(fields))
    legacy = {**fields, "policy_id": stored_policy_id}
    assert "renderer_version" not in legacy
    assert "prompt_template_text" not in legacy
    assert "code_state" not in legacy

    decoded = load_policy_manifest(PlatformSchemas(repository_root), legacy)

    verify_policy_manifest(decoded)
    assert decoded.policy_id == stored_policy_id
    assert decoded.renderer_version is None
    assert decoded.renderer_sha256 is None
    assert decoded.prompt_template_text is None
    assert decoded.code_state == "unverifiable"
    assert not decoded.source_provenance_verified
    assert decoded.source_provenance_failure_reason == "legacy_schema_missing_provenance"
    PlatformSchemas(repository_root).validate("policy_package", decoded.to_dict())


def test_invalid_run_manifest_blocks_export_before_any_file_is_written(
    repository_root: Path, tmp_path: Path
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    control.submit({"model": "missing-every-other-frozen-input"})
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="run_manifest"):
        export_evidence(control, output)

    assert not output.exists()


def test_invalid_stored_gate_report_fails_before_export_derivation(
    tmp_path: Path,
    passing_evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    invalid_report = dict(candidate.gate_report)
    invalid_report.pop("dataset_fingerprint")
    monkeypatch.setattr(
        control,
        "list_candidates",
        lambda: [dataclasses.replace(candidate, gate_report=invalid_report)],
    )
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="gate_report"):
        export_evidence(control, output)

    assert not output.exists()


def test_stored_submission_digest_mismatch_blocks_export_before_writes(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    submission_id = control.submit({"model": "original"})
    control.connection.execute(
        "UPDATE submissions SET request_json = ? WHERE submission_id = ?",
        (canonical_json_bytes({"model": "tampered"}).decode(), submission_id),
    )
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="submission digest"):
        export_evidence(control, output)

    assert not output.exists()


def test_stored_gate_report_digest_mismatch_blocks_export_before_derivation(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    tampered_report = copy.deepcopy(candidate.gate_report)
    tampered_report["accuracy"]["observed"] = 0.01
    control.connection.execute(
        "UPDATE candidates SET gate_report_json = ? WHERE candidate_id = ?",
        (canonical_json_bytes(tampered_report).decode(), candidate.candidate_id),
    )
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="gate_report digest"):
        export_evidence(control, output)

    assert not output.exists()


def test_passing_gate_report_with_reasons_blocks_export_before_derivation(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    invalid_report = {**candidate.gate_report, "reasons": ["blocking evidence retained"]}
    encoded = canonical_json_bytes(invalid_report)
    control.connection.execute(
        "UPDATE candidates SET gate_report_json = ?, gate_report_sha256 = ? WHERE candidate_id = ?",
        (encoded.decode(), sha256_bytes(encoded), candidate.candidate_id),
    )
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="gate_report"):
        export_evidence(control, output)

    assert not output.exists()


def test_malformed_stored_audit_details_fail_as_contract_error(
    tmp_path: Path,
) -> None:
    control = ControlStore(tmp_path / "control.db", reviewer_identity="local-reviewer")
    control.migrate()
    control.connection.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "audit-corrupt",
            "fixture.corrupt",
            "system",
            "fixture",
            "{",
            "2026-08-18T00:00:00+00:00",
        ),
    )
    output = tmp_path / "export"

    with pytest.raises(ContractValidationError, match="audit_event"):
        export_evidence(control, output)

    assert not output.exists()
