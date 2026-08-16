from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pixelgym.platform.control_store import (
    AuthorizationError,
    ConflictError,
    ControlStore,
    TransitionError,
)
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.gates import evaluate_gates
from pixelgym.platform.immutable_store import ImmutableStoreError, LocalImmutableStore
from pixelgym.platform.policy import build_policy_manifest, prompt_template
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


def test_candidate_registration_rejects_mismatched_or_self_inconsistent_evidence(
    tmp_path: Path, passing_evidence
) -> None:
    import dataclasses

    policy, summary, report = passing_evidence
    control = _control(tmp_path)
    with pytest.raises(ValueError, match="identities"):
        control.register_candidate(
            source_run_id="different-run", policy=policy, gate_report=report, artifacts=[]
        )
    fabricated = dataclasses.replace(
        report,
        accuracy=dataclasses.replace(report.accuracy, passed=False),
        overall_passed=True,
    )
    with pytest.raises(ValueError, match="failed components"):
        control.register_candidate(
            source_run_id=summary.run_id,
            policy=policy,
            gate_report=fabricated,
            artifacts=[],
        )


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

    with pytest.raises(TransitionError, match="approved|gate report"):
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

    with pytest.raises(TransitionError, match="approval evidence no longer matches"):
        restoring.restore_active()
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


def test_deploy_failure_preserves_active_and_repeated_rollbacks_follow_event_order(
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
    coordinator.deploy(second.candidate_id, actor="local-reviewer", reason="second")
    restored_first = coordinator.rollback(actor="local-reviewer", reason="rehearsal")
    restored_second = coordinator.rollback(actor="local-reviewer", reason="repeat rehearsal")

    assert restored_first.candidate_id == first.candidate_id
    assert restored_second.candidate_id == second.candidate_id
    assert [event["candidate_id"] for event in control.deployment_history()] == [
        first.candidate_id,
        second.candidate_id,
        first.candidate_id,
        second.candidate_id,
    ]
    assert [event["action"] for event in control.deployment_history()] == [
        "deploy",
        "deploy",
        "rollback",
        "rollback",
    ]
    assert all(
        "previous_deployment_id" not in event for event in control.deployment_history()
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

    with pytest.raises(TransitionError, match="no previous"):
        coordinator.rollback(actor="local-reviewer", reason="no prior event")


def test_store_rejects_rollback_to_any_candidate_except_previous_event(
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

    with pytest.raises(TransitionError, match="previous deployment event"):
        control.activate(
            second.candidate_id,
            actor="local-reviewer",
            reason="invalid rollback target",
            action="rollback",
            expected_deployment_id=current.deployment_id,
            expected_generation=generation,
        )


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
