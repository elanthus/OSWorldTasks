from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pixelgym.platform import control_store
from pixelgym.platform.control_store import (
    AuthorizationError,
    ConflictError,
    ControlStore,
    DeploymentRecord,
    TransitionError,
)
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import (
    ImmutableStore,
    ImmutableStoreError,
    LocalImmutableStore,
)
from pixelgym.platform.mlflow_tracking import TrackingMirrorError
from pixelgym.platform.policy import build_policy_manifest, prompt_template
from pixelgym.platform.schema_validation import ContractValidationError
from pixelgym.platform.source_provenance import SOURCE_PROVENANCE_SCHEMA_VERSION, SourceProvenance


def _control(tmp_path: Path) -> ControlStore:
    counter = iter(f"2000-01-01T00:00:{second:02d}Z" for second in range(60))
    control = ControlStore(
        tmp_path / "control.db",
        reviewer_identity="local-reviewer",
        now=lambda: next(counter),
    )
    control.migrate()
    return control


def _disable_approval_append_only_guards(control: ControlStore) -> None:
    """Simulate an externally corrupted database for read-boundary tests."""
    control.connection.execute("DROP TRIGGER approvals_no_update")
    control.connection.execute("DROP TRIGGER approvals_no_delete")


def test_file_runtime_connection_applies_wal_and_configured_busy_timeout(
    tmp_path: Path,
) -> None:
    control = ControlStore(
        tmp_path / "control.db",
        reviewer_identity="local-reviewer",
        busy_timeout_ms=237,
    )
    control.migrate()

    assert control.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert control.connection.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert control.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 237
    assert control.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_memory_and_file_uri_connections_use_documented_journal_modes(
    tmp_path: Path,
) -> None:
    memory = ControlStore(
        ":memory:", reviewer_identity="local-reviewer", busy_timeout_ms=149
    )
    file_uri = ControlStore(
        f"{(tmp_path / 'uri-control.db').as_uri()}?mode=rwc",
        reviewer_identity="local-reviewer",
        busy_timeout_ms=151,
    )
    named_shared_memory = ControlStore(
        f"{(tmp_path / 'named-shared-memory').as_uri()}?mode=memory&cache=shared",
        reviewer_identity="local-reviewer",
        busy_timeout_ms=153,
    )
    anonymous_shared_memory = ControlStore(
        "file::memory:?cache=shared",
        reviewer_identity="local-reviewer",
        busy_timeout_ms=155,
    )

    assert memory.connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert memory.connection.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert memory.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 149
    assert memory.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert file_uri.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert file_uri.connection.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert file_uri.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 151
    assert file_uri.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert named_shared_memory.connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert named_shared_memory.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 153
    assert anonymous_shared_memory.connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert anonymous_shared_memory.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 155


@pytest.mark.parametrize("value", ["not-an-integer", "0", "-1"])
def test_configured_busy_timeout_rejects_invalid_environment_value(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("PIXELGYM_SQLITE_BUSY_TIMEOUT_MS", value)

    with pytest.raises(ValueError, match="PIXELGYM_SQLITE_BUSY_TIMEOUT_MS"):
        control_store.configured_busy_timeout_ms()


def test_configured_busy_timeout_uses_default_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PIXELGYM_SQLITE_BUSY_TIMEOUT_MS", raising=False)
    assert control_store.configured_busy_timeout_ms() == 5_000

    monkeypatch.setenv("PIXELGYM_SQLITE_BUSY_TIMEOUT_MS", "237")
    assert control_store.configured_busy_timeout_ms() == 237


def test_sqlite_locked_operational_error_is_not_mapped_to_contention() -> None:
    error = sqlite3.OperationalError("database table is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_LOCKED

    with pytest.raises(sqlite3.OperationalError) as caught:
        control_store._raise_mapped_contention(error)

    assert caught.value is error


def test_require_migrated_rejects_missing_active_pointer_singleton(tmp_path: Path) -> None:
    control = _control(tmp_path)
    control.connection.execute("DELETE FROM active_pointer WHERE singleton = 1")

    with pytest.raises(RuntimeError, match="active_pointer singleton row is missing"):
        control.require_migrated()

    control.migrate()
    control.require_migrated()


def test_duplicate_submission_is_idempotent_and_changed_input_is_new(tmp_path: Path) -> None:
    control = _control(tmp_path)
    first = control.submit({"model": "a"})
    assert control.submit({"model": "a"}) == first
    assert control.submit({"model": "b"}) != first


def test_gate_failure_blocks_direct_approval_and_passing_gates_do_not_autoapprove(
    tmp_path: Path, passing_evidence, gate_policy, policy_factory
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    eligible = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[]
    )
    assert eligible.state.value == "Eligible"
    failed_summary = __import__("dataclasses").replace(summary, accuracy=0.79)
    failed_policy = policy_factory(model="failed-exact-model")
    failed_summary = __import__("dataclasses").replace(
        failed_summary, policy_id=failed_policy.policy_id, run_id="run-failed"
    )
    from pixelgym.platform.gates import evaluate_gates

    failed = control.register_candidate(
        source_run_id="run-failed",
        policy=failed_policy,
        gate_report=evaluate_gates(gate_policy, failed_summary),
        artifacts=[],
    )
    with pytest.raises(TransitionError, match="eligible"):
        control.approve(
            failed.candidate_id,
            actor="local-reviewer",
            reason="should fail",
            gate_report_sha256=failed.gate_report_sha256,
        )


def test_schema_invalid_approval_and_deployment_fail_before_authoritative_changes(
    tmp_path: Path,
    passing_evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    validate = control.schemas.validate

    def reject_approval(contract: str, value: object) -> None:
        if contract == "approval":
            raise ContractValidationError("approval violates its frozen schema")
        validate(contract, value)

    monkeypatch.setattr(control.schemas, "validate", reject_approval)
    with pytest.raises(ContractValidationError, match="approval"):
        control.approve(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="reviewed",
            gate_report_sha256=candidate.gate_report_sha256,
        )
    with pytest.raises(KeyError):
        control.get_approval(candidate.candidate_id)
    assert control.get_candidate(candidate.candidate_id).state.value == "Eligible"

    monkeypatch.setattr(control.schemas, "validate", validate)
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )

    def reject_deployment(contract: str, value: object) -> None:
        if contract == "deployment":
            raise ContractValidationError("deployment violates its frozen schema")
        validate(contract, value)

    monkeypatch.setattr(control.schemas, "validate", reject_deployment)
    with pytest.raises(ContractValidationError, match="deployment"):
        control.activate(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="deploy",
            action="deploy",
            expected_deployment_id=None,
            expected_generation=0,
        )
    assert control.deployment_history() == []
    assert control.active() == (None, 0)


def test_approval_and_deployment_revalidate_stored_candidate_evidence(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence

    approval_root = tmp_path / "approval"
    approval_root.mkdir()
    approval_control = _control(approval_root)
    approval_candidate = approval_control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    approval_control.connection.execute(
        "UPDATE candidates SET policy_json = '{}' WHERE candidate_id = ?",
        (approval_candidate.candidate_id,),
    )
    with pytest.raises(ContractValidationError, match="policy_package"):
        approval_control.approve(
            approval_candidate.candidate_id,
            actor="local-reviewer",
            reason="must revalidate",
            gate_report_sha256=approval_candidate.gate_report_sha256,
        )
    with pytest.raises(KeyError):
        approval_control.get_approval(approval_candidate.candidate_id)

    deployment_root = tmp_path / "deployment"
    deployment_root.mkdir()
    deployment_control = _control(deployment_root)
    deployment_candidate = deployment_control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    deployment_control.approve(
        deployment_candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed before corruption",
        gate_report_sha256=deployment_candidate.gate_report_sha256,
    )
    deployment_control.connection.execute(
        "UPDATE candidates SET gate_report_json = '{}' WHERE candidate_id = ?",
        (deployment_candidate.candidate_id,),
    )
    with pytest.raises(ContractValidationError, match="gate_report"):
        deployment_control.activate(
            deployment_candidate.candidate_id,
            actor="local-reviewer",
            reason="must revalidate",
            action="deploy",
            expected_deployment_id=None,
            expected_generation=0,
        )
    assert deployment_control.deployment_history() == []
    assert deployment_control.active() == (None, 0)


def test_reapproval_rejects_approved_candidate_without_approval_evidence(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    _disable_approval_append_only_guards(control)
    control.connection.execute(
        "DELETE FROM approvals WHERE candidate_id = ?",
        (candidate.candidate_id,),
    )

    with pytest.raises(
        ContractValidationError, match="approved candidate has no approval evidence"
    ):
        control.approve(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="reviewed",
            gate_report_sha256=candidate.gate_report_sha256,
        )

    assert control.get_candidate(candidate.candidate_id).state.value == "Approved"
    with pytest.raises(KeyError):
        control.get_approval(candidate.candidate_id)


def test_idempotent_reapproval_revalidates_stored_approval_evidence(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    _disable_approval_append_only_guards(control)
    control.connection.execute(
        "UPDATE approvals SET created_at_utc = '' WHERE candidate_id = ?",
        (candidate.candidate_id,),
    )

    with pytest.raises(ContractValidationError, match="approval"):
        control.approve(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="reviewed",
            gate_report_sha256=candidate.gate_report_sha256,
        )

    assert control.get_candidate(candidate.candidate_id).state.value == "Approved"
    assert control.deployment_history() == []


def test_activation_rejects_approval_policy_identity_mismatch(
    tmp_path: Path,
    passing_evidence,
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.approve(
        candidate.candidate_id,
        actor="local-reviewer",
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    _disable_approval_append_only_guards(control)
    control.connection.execute(
        "UPDATE approvals SET policy_id = ? WHERE candidate_id = ?",
        ("sha256:" + "0" * 64, candidate.candidate_id),
    )

    with pytest.raises(
        ContractValidationError, match="approval policy identity does not match candidate"
    ):
        control.activate(
            candidate.candidate_id,
            actor="local-reviewer",
            reason="must remain blocked",
            action="deploy",
            expected_deployment_id=None,
            expected_generation=0,
        )

    assert control.deployment_history() == []
    assert control.active() == (None, 0)


def test_candidate_registration_rejects_mismatched_or_self_inconsistent_evidence(
    tmp_path: Path, passing_evidence
) -> None:
    import dataclasses

    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    invalid_schema = dataclasses.replace(report, schema_version="unsupported-report")
    with pytest.raises(ContractValidationError, match="gate_report"):
        control.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=invalid_schema,
            artifacts=[],
        )
    assert control.list_candidates() == []
    with pytest.raises(ValueError, match="identities"):
        control.register_candidate(
            source_run_id="different-run", policy=policy, gate_report=report, artifacts=[]
        )
    fabricated = dataclasses.replace(
        report,
        accuracy=dataclasses.replace(report.accuracy, passed=False),
        overall_passed=True,
    )
    with pytest.raises(ContractValidationError, match="gate_report"):
        control.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=fabricated,
            artifacts=[],
        )


def test_candidate_reads_lazily_revalidate_a_manifest_corrupted_after_registration(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id,
        policy=policy,
        gate_report=report,
        artifacts=[],
    )
    control.connection.execute(
        "UPDATE candidates SET policy_json = '{}' WHERE candidate_id = ?",
        (candidate.candidate_id,),
    )

    with pytest.raises(ContractValidationError, match="policy_package"):
        control.get_candidate(candidate.candidate_id)
    with pytest.raises(ContractValidationError, match="policy_package"):
        control.list_candidates(limit=1)


def test_distinct_source_provenance_diagnostics_have_distinct_candidate_identities(
    tmp_path: Path, passing_evidence, gate_policy
) -> None:
    _policy, summary, _report = passing_evidence
    control = _control(tmp_path)

    def policy_for(reason: str):
        return build_policy_manifest(
            provider="scripted-demo",
            model="day3-replay-revised-v2",
            prompt_name="pixelgym-grounding",
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
                None,
                None,
                "unverifiable",
                "none",
                reason,
            ),
            dependency_lock_sha256="a" * 64,
        )

    first_policy = policy_for("manifest_missing")
    second_policy = policy_for("source_digest_mismatch")
    assert first_policy.policy_id != second_policy.policy_id
    for policy in (first_policy, second_policy):
        failed_summary = __import__("dataclasses").replace(
            summary,
            policy_id=policy.policy_id,
            code_state="unverifiable",
            code_provenance_verified=False,
        )
        candidate = control.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=evaluate_gates(gate_policy, failed_summary),
            artifacts=[],
            summary=failed_summary,
        )
        assert candidate.policy.policy_id == policy.policy_id
def test_approval_requires_server_identity_reason_and_exact_report_digest(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[]
    )
    with pytest.raises(AuthorizationError):
        control.approve(candidate.candidate_id, actor="browser-field", reason="ok", gate_report_sha256=candidate.gate_report_sha256)
    with pytest.raises(ValueError, match="reason"):
        control.approve(candidate.candidate_id, actor="local-reviewer", reason=" ", gate_report_sha256=candidate.gate_report_sha256)
    with pytest.raises(TransitionError, match="digest"):
        control.approve(candidate.candidate_id, actor="local-reviewer", reason="ok", gate_report_sha256="wrong")
    approval = control.approve(candidate.candidate_id, actor="local-reviewer", reason="reviewed evidence", gate_report_sha256=candidate.gate_report_sha256)
    assert approval["actor"] == "local-reviewer"
    assert control.get_candidate(candidate.candidate_id).state.value == "Approved"


def test_deployment_coordinator_rejects_unapproved_candidate_before_smoke(
    tmp_path: Path, passing_evidence
) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(
        source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[]
    )
    smoke_called = False

    def smoke(_candidate):
        nonlocal smoke_called
        smoke_called = True
        return True

    coordinator = DeploymentCoordinator(
        control=control,
        store=LocalImmutableStore(tmp_path / "immutable"),
        load_and_smoke=smoke,
    )

    with pytest.raises(TransitionError, match="approved"):
        coordinator.deploy(candidate.candidate_id, actor="local-reviewer", reason="not reviewed")
    assert not smoke_called


@pytest.mark.parametrize("invalid_active", ["unapproved", "gate_failed"])
def test_serving_restore_rejects_unapproved_or_gate_failed_active_policy(
    tmp_path: Path, passing_evidence, invalid_active: str
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    deployed = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    if invalid_active == "unapproved":
        control.connection.execute(
            "UPDATE candidates SET state = ? WHERE candidate_id = ?",
            ("Eligible", candidate.candidate_id),
        )
    else:
        failed_report = {**candidate.gate_report, "overall_passed": False}
        encoded = canonical_json_bytes(failed_report)
        control.connection.execute(
            "UPDATE candidates SET gate_report_json = ? WHERE candidate_id = ?",
            (encoded.decode(), candidate.candidate_id),
        )
    smoke_called = False
    activated: list[object] = []

    def smoke(_candidate):
        nonlocal smoke_called
        smoke_called = True
        return True

    restoring = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=smoke,
        on_activated=lambda deployment, prepared: activated.append(deployment),
    )

    with pytest.raises(
        (TransitionError, ContractValidationError), match="approved|gate.?report"
    ):
        restoring.restore_active()
    assert control.active()[0] == deployed
    assert not smoke_called
    assert not activated


def test_serving_restore_rejects_gate_report_rewritten_after_approval(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    rewritten_report = {**candidate.gate_report, "run_id": "rewritten-after-approval"}
    encoded = canonical_json_bytes(rewritten_report)
    control.connection.execute(
        "UPDATE candidates SET gate_report_json = ?, gate_report_sha256 = ? WHERE candidate_id = ?",
        (encoded.decode(), sha256_bytes(encoded), candidate.candidate_id),
    )
    smoke_called = False

    def smoke(_candidate):
        nonlocal smoke_called
        smoke_called = True
        return True

    restoring = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=smoke
    )

    with pytest.raises(ContractValidationError, match="run identity"):
        restoring.restore_active()
    assert not smoke_called


def test_serving_restore_uses_validated_candidate_snapshot_without_second_read(
    tmp_path: Path,
    passing_evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    loaded: list[str] = []

    def reject_second_read(_candidate_id: str):
        raise AssertionError("validated candidate must not be read again")

    monkeypatch.setattr(control, "get_candidate", reject_second_read)
    restoring = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=lambda item: loaded.append(item.candidate_id) or True,
    )

    restored = restoring.restore_active()

    assert restored is not None
    assert restored.candidate_id == candidate.candidate_id
    assert loaded == [candidate.candidate_id]


def test_serving_restore_rejects_legacy_policy_provenance_before_artifacts_or_smoke(
    tmp_path: Path,
    passing_evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    legacy_policy = candidate.policy.to_dict()
    for field_name in (
        "code_state",
        "source_tree_sha256",
        "source_provenance_verified",
        "source_provenance_failure_reason",
    ):
        legacy_policy.pop(field_name)
    legacy_identity = dict(legacy_policy)
    legacy_identity.pop("policy_id")
    legacy_policy_id = "sha256:" + sha256_bytes(canonical_json_bytes(legacy_identity))
    legacy_policy["policy_id"] = legacy_policy_id
    rewritten_report = {**candidate.gate_report, "policy_id": legacy_policy_id}
    report_bytes = canonical_json_bytes(rewritten_report)
    report_digest = sha256_bytes(report_bytes)
    _disable_approval_append_only_guards(control)
    control.connection.execute(
        "UPDATE candidates SET policy_id = ?, policy_json = ?, gate_report_json = ?, gate_report_sha256 = ? WHERE candidate_id = ?",
        (
            legacy_policy_id,
            canonical_json_bytes(legacy_policy).decode(),
            report_bytes.decode(),
            report_digest,
            candidate.candidate_id,
        ),
    )
    control.connection.execute(
        "UPDATE approvals SET policy_id = ?, gate_report_sha256 = ? WHERE candidate_id = ?",
        (legacy_policy_id, report_digest, candidate.candidate_id),
    )
    artifact_called = False
    smoke_called = False

    def verify_artifact(_reference):
        nonlocal artifact_called
        artifact_called = True

    def smoke(_candidate):
        nonlocal smoke_called
        smoke_called = True
        return True

    monkeypatch.setattr(store, "get_verified", verify_artifact)
    restoring = DeploymentCoordinator(control=control, store=store, load_and_smoke=smoke)

    with pytest.raises(ContractValidationError, match="source provenance"):
        restoring.restore_active()
    assert not artifact_called
    assert not smoke_called


def test_serving_restore_rejects_malformed_approval_before_artifacts_or_smoke(
    tmp_path: Path,
    passing_evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    _disable_approval_append_only_guards(control)
    control.connection.execute(
        "UPDATE approvals SET reason = '' WHERE candidate_id = ?",
        (candidate.candidate_id,),
    )
    artifact_called = False
    smoke_called = False

    def verify_artifact(_reference):
        nonlocal artifact_called
        artifact_called = True

    def smoke(_candidate):
        nonlocal smoke_called
        smoke_called = True
        return True

    monkeypatch.setattr(store, "get_verified", verify_artifact)
    restoring = DeploymentCoordinator(control=control, store=store, load_and_smoke=smoke)

    with pytest.raises(ContractValidationError, match="approval"):
        restoring.restore_active()
    assert not artifact_called
    assert not smoke_called


def test_serving_restore_aborts_startup_when_active_policy_smoke_fails(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    deployed = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    activated: list[object] = []
    restoring = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=lambda policy: False,
        on_activated=lambda deployment, prepared: activated.append(deployment),
    )

    with pytest.raises(TransitionError, match="deterministic smoke"):
        restoring.restore_active()
    assert control.active()[0] == deployed
    assert not activated


def _approved_candidate(control: ControlStore, passing_evidence, store: LocalImmutableStore, suffix: str):
    policy, summary, report = passing_evidence
    if suffix:
        from pixelgym.platform.policy import build_policy_manifest, prompt_template

        policy = build_policy_manifest(
            provider=policy.provider,
            model=policy.model + "-" + suffix,
            prompt_name=policy.prompt_name,
            prompt_version=policy.prompt_version,
            prompt=prompt_template(policy.prompt_version) + suffix,
            condition=policy.condition,
            parameters=policy.parameters,
            parser_version=policy.parser_version,
            scorer_version=policy.scorer_version,
            overlay_version=policy.overlay_version,
            target_semantics=policy.target_semantics,
            source_provenance=__import__("pixelgym.platform.source_provenance", fromlist=["SourceProvenance"]).SourceProvenance(
                "pixelgym-source-provenance-v1", policy.code_revision, policy.source_tree_sha256,
                policy.code_state, "git-build-inputs-v1"
            ),
            dependency_lock_sha256=policy.dependency_lock_sha256,
            model_alias_disclosure=policy.model_alias_disclosure,
        )
    reference = store.put_once(f"policy/{suffix or 'a'}.json", b"verified", media_type="application/json")
    candidate = control.register_candidate(
        source_run_id=summary.run_id + suffix,
        policy=policy,
        gate_report=__import__("dataclasses").replace(report, policy_id=policy.policy_id, run_id=summary.run_id + suffix),
        artifacts=[reference],
    )
    control.approve(candidate.candidate_id, actor="local-reviewer", reason="reviewed", gate_report_sha256=candidate.gate_report_sha256)
    return candidate


class _LifecycleMirror:
    def __init__(self, *, fail_alias: bool = False) -> None:
        self.fail_alias = fail_alias
        self.statuses: list[tuple[str, str, str]] = []
        self.champions: list[str] = []

    def mirror_candidate_status(
        self, policy_id: str, *, gate_status: str, approval_status: str
    ) -> None:
        self.statuses.append((policy_id, gate_status, approval_status))

    def set_champion(self, policy_id: str) -> None:
        if self.fail_alias:
            raise TrackingMirrorError("registry unavailable")
        self.champions.append(policy_id)


def test_deploy_mirrors_champion_after_activation_and_records_alias_failure(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    mirror = _LifecycleMirror()
    deployed = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=lambda policy: True,
        tracking=mirror,
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="activate")

    assert mirror.champions == [candidate.policy.policy_id]
    assert control.active()[0] == deployed

    failed_mirror = _LifecycleMirror(fail_alias=True)
    second = _approved_candidate(control, passing_evidence, store, "second-mirror")
    deployed_second = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=lambda policy: True,
        tracking=failed_mirror,
    ).deploy(second.candidate_id, actor="local-reviewer", reason="activate despite mirror")

    assert control.active()[0] == deployed_second
    event = control.audit_events()[-1]
    assert event["event_type"] == "tracking.reconciliation_required"
    assert event["details"]["operation"] == "set_champion_alias"


def test_reconcile_tracking_mirrors_authoritative_status_and_active_alias(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="activate")
    mirror = _LifecycleMirror()

    DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=lambda policy: True,
        tracking=mirror,
    ).reconcile_tracking()

    assert mirror.statuses == [(candidate.policy.policy_id, "eligible", "approved")]
    assert mirror.champions == [candidate.policy.policy_id]
    assert control.audit_events()[-1]["event_type"] == "tracking.reconciliation_resolved"


def test_deploy_failure_preserves_active_and_repeated_rollback_refuses_bad_source(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    first = _approved_candidate(control, passing_evidence, store, "")
    coordinator = DeploymentCoordinator(control=control, store=store, load_and_smoke=lambda policy: True)
    deployed_first = coordinator.deploy(first.candidate_id, actor="local-reviewer", reason="first")
    second = _approved_candidate(control, passing_evidence, store, "second")
    failing = DeploymentCoordinator(control=control, store=store, load_and_smoke=lambda policy: False)
    with pytest.raises(TransitionError, match="smoke"):
        failing.deploy(second.candidate_id, actor="local-reviewer", reason="bad")
    assert control.active()[0] == deployed_first
    deployed_second = coordinator.deploy(
        second.candidate_id, actor="local-reviewer", reason="second"
    )
    restored_first = coordinator.rollback(actor="local-reviewer", reason="rehearsal")
    with pytest.raises(TransitionError, match="no eligible known-good"):
        coordinator.rollback(actor="local-reviewer", reason="repeat rehearsal")

    assert restored_first.candidate_id == first.candidate_id
    assert [event["candidate_id"] for event in control.deployment_history()] == [
        first.candidate_id,
        second.candidate_id,
        first.candidate_id,
    ]
    assert [event["action"] for event in control.deployment_history()] == [
        "deploy",
        "deploy",
        "rollback",
    ]
    rollback_audit = control.audit_events()[-1]
    assert rollback_audit["event_type"] == "deployment.rollback"
    assert rollback_audit["details"]["abandoned_deployment_id"] == (
        deployed_second.deployment_id
    )
    assert rollback_audit["details"]["restored_deployment_id"] == (
        deployed_first.deployment_id
    )


def test_migrate_drops_legacy_deployment_link_without_losing_history(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    deployed = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN previous_deployment_id TEXT REFERENCES deployments(deployment_id)"
    )
    with pytest.raises(RuntimeError, match="legacy deployment links"):
        control.require_migrated()

    control.migrate()

    columns = {
        row["name"] for row in control.connection.execute("PRAGMA table_info(deployments)")
    }
    assert "previous_deployment_id" not in columns
    control.require_migrated()
    assert control.active()[0] == deployed
    assert control.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        control.connection.execute("UPDATE deployments SET actor = 'changed'")


def test_legacy_deployment_migration_rejects_schema_drift(
    tmp_path: Path, passing_evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(control=control, store=store, load_and_smoke=lambda policy: True).deploy(
        candidate.candidate_id, actor="local-reviewer", reason="first"
    )
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN previous_deployment_id TEXT REFERENCES deployments(deployment_id)"
    )
    changed_schema = control_store.SCHEMA.replace(
        "  generation INTEGER NOT NULL UNIQUE\n);",
        "  generation INTEGER NOT NULL UNIQUE,\n  release_channel TEXT NOT NULL DEFAULT 'stable'\n);",
    )
    monkeypatch.setattr(control_store, "SCHEMA", changed_schema)

    with pytest.raises(RuntimeError, match=r"stale schema .*release_channel"):
        control.migrate()
    columns = {
        row["name"] for row in control.connection.execute("PRAGMA table_info(deployments)")
    }
    assert "previous_deployment_id" in columns
    active_candidate = control.connection.execute(
        """SELECT deployments.candidate_id
        FROM active_pointer JOIN deployments USING(deployment_id)
        WHERE singleton = 1"""
    ).fetchone()
    assert active_candidate["candidate_id"] == candidate.candidate_id


def test_legacy_deployment_migration_preserves_unexpected_schema_and_data(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN previous_deployment_id TEXT REFERENCES deployments(deployment_id)"
    )
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN release_channel TEXT NOT NULL DEFAULT 'sentinel-channel'"
    )

    with pytest.raises(RuntimeError, match=r"stale schema .*release_channel"):
        control.migrate()

    columns = {
        row["name"] for row in control.connection.execute("PRAGMA table_info(deployments)")
    }
    assert {"previous_deployment_id", "release_channel"} <= columns
    row = control.connection.execute(
        "SELECT release_channel FROM deployments"
    ).fetchone()
    assert row["release_channel"] == "sentinel-channel"
    assert control.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_rejects_same_columns_with_missing_constraints(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "malformed.sqlite", reviewer_identity="local-reviewer")
    control.connection.executescript(control_store.SCHEMA)
    control.connection.execute("PRAGMA foreign_keys = OFF")
    control.connection.execute("DROP TABLE deployments")
    control.connection.execute(
        """CREATE TABLE deployments (
          deployment_id TEXT,
          candidate_id TEXT,
          policy_id TEXT,
          action TEXT,
          actor TEXT,
          reason TEXT,
          created_at_utc TEXT,
          generation INTEGER
        )"""
    )
    control.connection.executemany(
        "INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("bad-1", "missing", "policy", "invalid", "actor", "reason", "now", 1),
            ("bad-2", "missing", "policy", "invalid", "actor", "reason", "now", 1),
        ],
    )
    control.connection.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(
        RuntimeError,
        match=r"stale schema .*columns.*foreign_keys.*indexes.*checks.*triggers",
    ):
        control.require_migrated()
    with pytest.raises(
        RuntimeError,
        match=r"stale schema .*columns.*foreign_keys.*indexes.*checks.*triggers",
    ):
        control.migrate()

    assert control.connection.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 2
    assert control.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_rejects_hidden_generated_column_without_losing_history(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    deployed = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN poison INTEGER "
        "GENERATED ALWAYS AS (generation + 1) VIRTUAL"
    )

    with pytest.raises(RuntimeError, match=r"stale schema .*poison"):
        control.require_migrated()
    with pytest.raises(RuntimeError, match=r"stale schema .*poison"):
        control.migrate()

    row = control.connection.execute(
        "SELECT deployment_id, poison FROM deployments"
    ).fetchone()
    assert (row["deployment_id"], row["poison"]) == (deployed.deployment_id, 2)


def test_new_serving_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite"
    migrator = ControlStore(database, reviewer_identity="local-reviewer")
    migrator.migrate()
    migrator.connection.close()

    serving = ControlStore(database, reviewer_identity="local-reviewer")
    serving.require_migrated()

    assert serving.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        serving.connection.execute(
            "INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bad", "missing", "policy", "deploy", "actor", "reason", "now", 1),
        )


def test_wal_migration_restart_preserves_data_and_schema(tmp_path: Path) -> None:
    database = tmp_path / "restart.sqlite"
    migrator = ControlStore(database, reviewer_identity="local-reviewer")
    migrator.migrate()
    submission_id = migrator.submit({"model": "persisted"})
    migrator.connection.close()

    serving = ControlStore(database, reviewer_identity="local-reviewer")
    serving.require_migrated()

    assert serving.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert serving.get_submission(submission_id)["request"] == {"model": "persisted"}


def test_migration_ignores_check_text_in_sql_comment(tmp_path: Path) -> None:
    control = ControlStore(tmp_path / "commented-check.sqlite", reviewer_identity="local-reviewer")
    malformed_schema = control_store.SCHEMA.replace(
        "  action TEXT NOT NULL CHECK(action IN ('deploy', 'rollback')),",
        "  action TEXT NOT NULL, -- CHECK(action IN ('deploy', 'rollback'))",
    )
    assert malformed_schema != control_store.SCHEMA
    control.connection.executescript(malformed_schema)
    control.connection.execute("PRAGMA foreign_keys = OFF")
    control.connection.execute(
        "INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("bad", "missing", "policy", "invalid", "actor", "reason", "now", 1),
    )
    control.connection.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(RuntimeError, match=r"stale schema .*checks"):
        control.require_migrated()
    with pytest.raises(RuntimeError, match=r"stale schema .*checks"):
        control.migrate()

    assert control.connection.execute("SELECT action FROM deployments").fetchone()[0] == "invalid"


def test_legacy_deployment_migration_rolls_back_on_foreign_key_violation(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    ).deploy(candidate.candidate_id, actor="local-reviewer", reason="first")
    control.connection.execute(
        "ALTER TABLE deployments ADD COLUMN previous_deployment_id TEXT REFERENCES deployments(deployment_id)"
    )
    control.connection.execute("PRAGMA foreign_keys = OFF")
    control.connection.execute(
        "UPDATE active_pointer SET deployment_id = 'missing-deployment' WHERE singleton = 1"
    )
    control.connection.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(RuntimeError, match="violates foreign keys"):
        control.migrate()

    columns = {
        row["name"] for row in control.connection.execute("PRAGMA table_info(deployments)")
    }
    assert "previous_deployment_id" in columns
    assert control.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        control.connection.execute("UPDATE deployments SET actor = 'changed'")


def test_rollback_fails_when_no_previous_deployment_event_exists(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    first = _approved_candidate(control, passing_evidence, store, "")
    coordinator = DeploymentCoordinator(control=control, store=store, load_and_smoke=lambda policy: True)
    coordinator.deploy(first.candidate_id, actor="local-reviewer", reason="first")

    with pytest.raises(TransitionError, match="no eligible known-good"):
        coordinator.rollback(actor="local-reviewer", reason="no prior event")


def test_repeated_rollbacks_follow_last_known_good_lineage(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate_c = _approved_candidate(control, passing_evidence, store, "candidate-c")
    candidate_a = _approved_candidate(control, passing_evidence, store, "candidate-a")
    candidate_b = _approved_candidate(control, passing_evidence, store, "candidate-b")
    coordinator = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    )

    deployed_c = coordinator.deploy(
        candidate_c.candidate_id, actor="local-reviewer", reason="candidate C"
    )
    coordinator.deploy(
        candidate_a.candidate_id, actor="local-reviewer", reason="candidate A"
    )
    deployed_b = coordinator.deploy(
        candidate_b.candidate_id, actor="local-reviewer", reason="candidate B"
    )

    restored_a = coordinator.rollback(actor="local-reviewer", reason="abandon B")
    assert restored_a.candidate_id == candidate_a.candidate_id
    assert control.previous_target(restored_a).deployment_id == deployed_c.deployment_id

    restored_c = coordinator.rollback(actor="local-reviewer", reason="abandon A")
    assert restored_c.candidate_id == candidate_c.candidate_id
    with pytest.raises(TransitionError, match="no eligible known-good"):
        coordinator.rollback(actor="local-reviewer", reason="exhausted lineage")

    assert [event["candidate_id"] for event in control.deployment_history()] == [
        candidate_c.candidate_id,
        candidate_a.candidate_id,
        candidate_b.candidate_id,
        candidate_a.candidate_id,
        candidate_c.candidate_id,
    ]
    rollback_audits = [
        event
        for event in control.audit_events()
        if event["event_type"] == "deployment.rollback"
    ]
    assert rollback_audits[0]["details"]["abandoned_deployment_id"] == (
        deployed_b.deployment_id
    )
    assert rollback_audits[1]["details"]["restored_deployment_id"] == (
        deployed_c.deployment_id
    )


def test_fresh_deploy_resets_the_abandoned_rollback_chain(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate_c = _approved_candidate(control, passing_evidence, store, "candidate-c")
    candidate_a = _approved_candidate(control, passing_evidence, store, "candidate-a")
    candidate_b = _approved_candidate(control, passing_evidence, store, "candidate-b")
    coordinator = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    )

    coordinator.deploy(candidate_c.candidate_id, actor="local-reviewer", reason="C")
    coordinator.deploy(candidate_a.candidate_id, actor="local-reviewer", reason="A")
    coordinator.deploy(candidate_b.candidate_id, actor="local-reviewer", reason="B")
    restored_a = coordinator.rollback(actor="local-reviewer", reason="abandon B")
    assert control.previous_target(restored_a).candidate_id == candidate_c.candidate_id

    redeployed_b = coordinator.deploy(
        candidate_b.candidate_id, actor="local-reviewer", reason="fresh explicit B"
    )
    restored_a_again = coordinator.rollback(
        actor="local-reviewer", reason="new deploy reset the chain"
    )

    assert redeployed_b.action == "deploy"
    assert restored_a_again.candidate_id == candidate_a.candidate_id
    assert [event["action"] for event in control.deployment_history()] == [
        "deploy",
        "deploy",
        "deploy",
        "rollback",
        "deploy",
        "rollback",
    ]


def test_pre_lineage_history_resolves_without_rewriting_stored_events(
    tmp_path: Path, passing_evidence
) -> None:
    database = tmp_path / "control.db"
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate_c = _approved_candidate(control, passing_evidence, store, "candidate-c")
    candidate_a = _approved_candidate(control, passing_evidence, store, "candidate-a")
    candidate_b = _approved_candidate(control, passing_evidence, store, "candidate-b")
    rows = [
        (
            "old-deploy-c",
            candidate_c.candidate_id,
            candidate_c.policy.policy_id,
            "deploy",
            1,
        ),
        (
            "old-deploy-a",
            candidate_a.candidate_id,
            candidate_a.policy.policy_id,
            "deploy",
            2,
        ),
        (
            "old-deploy-b",
            candidate_b.candidate_id,
            candidate_b.policy.policy_id,
            "deploy",
            3,
        ),
        (
            "old-rollback-a",
            candidate_a.candidate_id,
            candidate_a.policy.policy_id,
            "rollback",
            4,
        ),
    ]
    for deployment_id, candidate_id, policy_id, action, generation in rows:
        control.connection.execute(
            """INSERT INTO deployments(
                deployment_id, candidate_id, policy_id, action, actor, reason,
                created_at_utc, generation
            ) VALUES (?, ?, ?, ?, 'local-reviewer', 'pre-change event', ?, ?)""",
            (
                deployment_id,
                candidate_id,
                policy_id,
                action,
                f"2000-01-01T00:01:{generation:02d}Z",
                generation,
            ),
        )
    control.connection.execute(
        "UPDATE active_pointer SET deployment_id = 'old-rollback-a', generation = 4 "
        "WHERE singleton = 1"
    )
    original_history = control.deployment_history()
    control.connection.close()

    reopened = ControlStore(database, reviewer_identity="local-reviewer")
    reopened.migrate()
    current, generation = reopened.active()

    assert current is not None
    assert generation == 4
    assert reopened.previous_target(current).deployment_id == "old-deploy-c"
    assert reopened.deployment_history() == original_history
    restarted = DeploymentCoordinator(
        control=reopened, store=store, load_and_smoke=lambda policy: True
    )
    assert restarted.restore_active() == current
    assert restarted.rollback(
        actor="local-reviewer", reason="resolved after restart"
    ).candidate_id == candidate_c.candidate_id


def test_store_rejects_rollback_to_any_candidate_except_known_good_target(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    first = _approved_candidate(control, passing_evidence, store, "")
    second = _approved_candidate(control, passing_evidence, store, "second")
    coordinator = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    )
    first_event = coordinator.deploy(first.candidate_id, actor="local-reviewer", reason="first")
    current = coordinator.deploy(second.candidate_id, actor="local-reviewer", reason="second")
    _, generation = control.active()

    target = control.previous_target(current)
    assert target.deployment_id == first_event.deployment_id
    assert target.candidate_id == first.candidate_id

    with pytest.raises(TransitionError, match="eligible known-good deployment"):
        control.activate(
            second.candidate_id,
            actor="local-reviewer",
            reason="invalid rollback target",
            action="rollback",
            expected_deployment_id=current.deployment_id,
            expected_generation=generation,
        )


@pytest.mark.parametrize(
    "failure",
    ["unapproved", "corrupt", "missing", "incompatible", "unhealthy", "expired"],
)
def test_rollback_reverifies_known_good_target_before_activation(
    tmp_path: Path, passing_evidence, failure: str
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    target = _approved_candidate(control, passing_evidence, store, "target")
    active_candidate = _approved_candidate(control, passing_evidence, store, "active")
    setup = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    )
    setup.deploy(target.candidate_id, actor="local-reviewer", reason="target")
    active = setup.deploy(
        active_candidate.candidate_id, actor="local-reviewer", reason="active"
    )
    rollback_store: ImmutableStore = store

    if failure == "unapproved":
        control.connection.execute(
            "UPDATE candidates SET state = 'Eligible' WHERE candidate_id = ?",
            (target.candidate_id,),
        )
    elif failure == "corrupt":
        artifact = target.artifacts[0]
        (tmp_path / "immutable" / "objects" / artifact.logical_key).write_bytes(
            b"corrupt"
        )
    elif failure == "missing":
        artifact = target.artifacts[0]
        (tmp_path / "immutable" / "objects" / artifact.logical_key).unlink()
    elif failure == "expired":

        class ExpiredStore:
            def get_verified(self, _reference) -> bytes:
                raise ImmutableStoreError("immutable retention window expired")

        rollback_store = ExpiredStore()

    def smoke(_candidate) -> bool:
        if failure == "incompatible":
            raise DeploymentSmokeError("candidate is incompatible with the serving API")
        return failure != "unhealthy"

    coordinator = DeploymentCoordinator(
        control=control, store=rollback_store, load_and_smoke=smoke
    )
    with pytest.raises(
        (TransitionError, ContractValidationError, ImmutableStoreError, DeploymentSmokeError)
    ):
        coordinator.rollback(actor="local-reviewer", reason=f"reject {failure}")

    assert control.active()[0] == active
    assert len(control.deployment_history()) == 2


def test_corrupt_artifact_blocks_activation(tmp_path: Path, passing_evidence) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    (tmp_path / "immutable/objects/policy/a.json").write_bytes(b"corrupt")
    coordinator = DeploymentCoordinator(control=control, store=store, load_and_smoke=lambda policy: True)
    with pytest.raises(ImmutableStoreError):
        coordinator.deploy(candidate.candidate_id, actor="local-reviewer", reason="must fail")
    assert control.active()[0] is None


def test_append_only_tables_reject_updates_and_deletes(tmp_path: Path, passing_evidence) -> None:
    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    candidate = control.register_candidate(source_run_id=summary.run_id, policy=policy, gate_report=report, artifacts=[])
    control.approve(candidate.candidate_id, actor="local-reviewer", reason="ok", gate_report_sha256=candidate.gate_report_sha256)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        control.connection.execute("DELETE FROM approvals")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        control.connection.execute("UPDATE audit_events SET actor = 'other'")


def test_stale_compare_and_swap_loses_cleanly(tmp_path: Path, passing_evidence) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate = _approved_candidate(control, passing_evidence, store, "")
    control.activate(candidate.candidate_id, actor="local-reviewer", reason="winner", action="deploy", expected_deployment_id=None, expected_generation=0)
    with pytest.raises(ConflictError, match="concurrently"):
        control.activate(candidate.candidate_id, actor="local-reviewer", reason="loser", action="deploy", expected_deployment_id=None, expected_generation=0)


def test_coordinator_rejects_stale_rendered_deploy_and_rollback_state(
    tmp_path: Path, passing_evidence
) -> None:
    control = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    first = _approved_candidate(control, passing_evidence, store, "first")
    second = _approved_candidate(control, passing_evidence, store, "second")
    coordinator = DeploymentCoordinator(
        control=control, store=store, load_and_smoke=lambda policy: True
    )
    deployed_first = coordinator.deploy(first.candidate_id, actor="local-reviewer", reason="first")
    _rendered_deployment, rendered_generation = control.active()
    deployed_second = coordinator.deploy(second.candidate_id, actor="local-reviewer", reason="second")

    with pytest.raises(ConflictError, match="changed concurrently"):
        coordinator.deploy(
            first.candidate_id, actor="local-reviewer", reason="stale deploy form",
            expected_deployment_id=deployed_first.deployment_id,
            expected_generation=rendered_generation,
        )
    assert control.active()[0] == deployed_second

    rolled_back = coordinator.rollback(actor="local-reviewer", reason="first rollback")
    with pytest.raises(ConflictError, match="changed concurrently"):
        coordinator.rollback(
            actor="local-reviewer", reason="stale rollback form",
            expected_deployment_id=deployed_second.deployment_id,
            expected_generation=deployed_second.generation,
        )
    assert control.active()[0] == rolled_back


def test_concurrent_deploy_and_rollback_have_one_store_winner(
    tmp_path: Path, passing_evidence
) -> None:
    primary = _control(tmp_path)
    store = LocalImmutableStore(tmp_path / "immutable")
    candidate_a = _approved_candidate(primary, passing_evidence, store, "candidate-a")
    candidate_b = _approved_candidate(primary, passing_evidence, store, "candidate-b")
    candidate_c = _approved_candidate(primary, passing_evidence, store, "candidate-c")
    setup = DeploymentCoordinator(
        control=primary, store=store, load_and_smoke=lambda policy: True
    )
    setup.deploy(candidate_a.candidate_id, actor="local-reviewer", reason="A")
    current = setup.deploy(
        candidate_b.candidate_id, actor="local-reviewer", reason="B"
    )
    _, generation = primary.active()

    secondary = ControlStore(
        tmp_path / "control.db", reviewer_identity="local-reviewer"
    )
    secondary.require_migrated()
    interleaved = threading.Barrier(2)

    def smoke(_candidate) -> bool:
        interleaved.wait(timeout=2)
        return True

    deploying = DeploymentCoordinator(
        control=primary, store=store, load_and_smoke=smoke
    )
    rolling_back = DeploymentCoordinator(
        control=secondary, store=store, load_and_smoke=smoke
    )

    def deploy() -> DeploymentRecord | None:
        try:
            return deploying.deploy(
                candidate_c.candidate_id,
                actor="local-reviewer",
                reason="concurrent deploy",
                expected_deployment_id=current.deployment_id,
                expected_generation=generation,
            )
        except ConflictError:
            return None

    def rollback() -> DeploymentRecord | None:
        try:
            return rolling_back.rollback(
                actor="local-reviewer",
                reason="concurrent rollback",
                expected_deployment_id=current.deployment_id,
                expected_generation=generation,
            )
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(operation) for operation in (deploy, rollback)]
        outcomes = [future.result(timeout=5) for future in futures]

    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    active_primary, primary_generation = primary.active()
    active_secondary, secondary_generation = secondary.active()
    assert active_primary == active_secondary == winners[0]
    assert primary_generation == secondary_generation == generation + 1
    assert primary.deployment_history()[-1]["deployment_id"] == (
        winners[0].deployment_id
    )
    assert primary.connection.execute(
        "SELECT COUNT(*) FROM active_pointer WHERE singleton = 1"
    ).fetchone()[0] == 1


def test_identical_audit_events_with_fixed_clock_have_distinct_ids(tmp_path: Path) -> None:
    control = ControlStore(
        tmp_path / "control.db",
        reviewer_identity="local-reviewer",
        now=lambda: "2000-01-01T00:00:00Z",
    )
    control.migrate()
    with control.transaction() as connection:
        control._audit(connection, "same", "actor", "subject", {"value": 1})
        control._audit(connection, "same", "actor", "subject", {"value": 1})

    events = control.audit_events()
    assert len(events) == 2
    assert events[0]["event_id"] != events[1]["event_id"]


def test_reads_wait_for_the_shared_connection_lock(tmp_path: Path) -> None:
    control = _control(tmp_path)
    control.submit({"model": "a"})
    started = threading.Event()

    def read() -> list[dict]:
        started.set()
        return control.list_submissions()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with control._lock:
            future = executor.submit(read)
            assert started.wait(timeout=1)
            assert not future.done()
        assert future.result(timeout=1)[0]["submission_id"].startswith("submission-")
