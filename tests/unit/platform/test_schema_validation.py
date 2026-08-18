from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from pixelgym.platform.contracts import GateReport, PolicyManifest
from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.policy import verify_policy_manifest
from pixelgym.platform.schema_validation import (
    CONTRACT_SCHEMA_FILES,
    ContractValidationError,
    PlatformSchemas,
    load_gate_policy,
    load_policy_manifest,
    load_price_catalog,
)
from scripts.export_platform_evidence import export_evidence

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
}


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
    }


def test_registry_inventory_matches_every_d41_contract(repository_root: Path) -> None:
    schemas = PlatformSchemas(repository_root)

    assert set(schemas.versions) == set(CONTRACT_SCHEMA_FILES)
    assert schemas.versions == {
        "run_manifest": "pixelgym-platform-run-manifest-v1",
        "gate_policy": "pixelgym-promotion-gate-policy-v1",
        "gate_report": "pixelgym-promotion-gate-report-v1",
        "policy_package": "pixelgym-grounding-policy-v1",
        "raw_response": "pixelgym-raw-response-v1",
        "approval": "pixelgym-policy-approval-v1",
        "deployment": "pixelgym-policy-deployment-v1",
        "audit_event": "pixelgym-platform-audit-event-v1",
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


def test_representative_python_contracts_validate(
    repository_root: Path, passing_evidence
) -> None:
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
    invalid_gate = json.loads(
        (repository_root / "config/promotion-gates.demo-v1.json").read_text()
    )
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
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["audit_event"]
    )
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
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["raw_response"]
    )
    value[field] = invalid

    with pytest.raises(ContractValidationError, match="JSON"):
        PlatformSchemas(repository_root).validate("raw_response", value)


def test_date_time_format_is_enforced(repository_root: Path, passing_evidence) -> None:
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["approval"]
    )
    value["created_at_utc"] = "not-a-timestamp"

    with pytest.raises(ContractValidationError, match="date-time"):
        PlatformSchemas(repository_root).validate("approval", value)


@pytest.mark.parametrize("field", ["gate_policy_version", "run_id"])
def test_gate_report_rejects_empty_provenance_identifiers(
    repository_root: Path,
    passing_evidence,
    field: str,
) -> None:
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["gate_report"]
    )
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
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["gate_report"]
    )
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


@pytest.mark.parametrize("field", ["dataset_fingerprint", "policy_id"])
def test_complete_run_manifest_requires_dataset_and_policy_provenance(
    repository_root: Path,
    passing_evidence,
    field: str,
) -> None:
    value = copy.deepcopy(
        _representatives(repository_root, passing_evidence)["run_manifest"]
    )
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


def test_former_v1_policy_remains_readable_with_identity_and_fail_closed_provenance(
    repository_root: Path, passing_evidence
) -> None:
    policy, _summary, _report = passing_evidence
    legacy = policy.to_dict()
    for field_name in (
        "code_state",
        "source_tree_sha256",
        "source_provenance_verified",
        "source_provenance_failure_reason",
    ):
        legacy.pop(field_name)
    identity = dict(legacy)
    identity.pop("policy_id")
    legacy["policy_id"] = "sha256:" + sha256_bytes(canonical_json_bytes(identity))

    decoded = load_policy_manifest(PlatformSchemas(repository_root), legacy)

    verify_policy_manifest(decoded)
    assert decoded.policy_id == legacy["policy_id"]
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
