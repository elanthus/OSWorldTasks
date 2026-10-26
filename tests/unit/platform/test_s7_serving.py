"""Offline failure-mode tests for S7 gates, reservations, source and report boundaries."""

from __future__ import annotations

import dataclasses
import json
import socket
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import create_archive, restore_archive
from pixelgym.platform.control_store import ControlStore, TransitionError
from pixelgym.platform.s7_budget import BudgetedScriptedTransport, S7CapReached, S7SendBudget
from pixelgym.platform.s7_evidence import inventory, stored_report, verify_seal
from pixelgym.platform.s7_policy import (
    EVIDENCE_LABEL,
    POLICY_SHA256,
    RULES,
    evaluate,
    policy_document,
    validate_report_rules,
)
from pixelgym.platform.s7_qualification import read_json, seal
from pixelgym.platform.s7_source import verify_snapshot, worker_binding, write_json
from pixelgym.platform.stateful_contracts import EvidenceBinding, StatefulPolicyPackage
from scripts.s7_serving import no_network
from tests.unit.platform.stateful_fixtures import v5_manifest


def bound_refs(package: StatefulPolicyPackage) -> list[Any]:
    from pixelgym.platform.contracts import ArtifactRef

    return [
        ArtifactRef(
            "fixture/" + str(i),
            "local://fixture/" + str(i),
            "1",
            digest[7:],
            0,
            "application/json",
            "retained",
        )
        for i, digest in enumerate((package.evidence.plan_sha256, package.evidence.summary_sha256))
    ]


def approved_archive(archive: Path) -> tuple[Path, str]:
    return archive, content_digest(json.loads((archive / "manifest.json").read_bytes()))


def fixture_host_source(monkeypatch: Any) -> None:
    """Synthetic checkout tests do not claim host import provenance."""
    monkeypatch.setattr("pixelgym.platform.s7_source.verify_host_source", lambda root: None)
    monkeypatch.setattr("pixelgym.platform.s7_qualification.verify_host_source", lambda root: None)


def candidate(
    role: str = "B", **metric_changes: Any
) -> tuple[StatefulPolicyPackage, dict[str, Any]]:
    manifest = v5_manifest()
    metrics = {name: threshold for name, _, threshold in RULES}
    metrics.update(metric_changes)
    summary = {
        "schema_version": "pixelgym-s7-summary-v1",
        "evidence_label": EVIDENCE_LABEL,
        "role": role,
        "metrics": metrics,
        "inputs": {},
        "binding": {
            "plan_sha256": "sha256:" + "a" * 64,
            "run_reference": "s7-unit-fixture",
            "v5_policy_id": manifest.policy_id,
            "package_source_sha256": "b" * 64,
        },
    }
    package = StatefulPolicyPackage.build(
        manifest=dataclasses.replace(manifest, provider=manifest.provider),
        model_alias_disclosure=None,
        package_source_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        max_steps=1 if role == "A" else 26,
        evidence_class="calibration",
        evidence=EvidenceBinding(
            "d56-calibration",
            "s7-unit-fixture",
            "sha256:" + "a" * 64,
            content_digest(summary),
            1,
            1,
            int(role == "B"),
        ),
        code_revision=manifest.code_revision,
        code_state="clean",
        source_tree_sha256="d" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )
    # Build an exact fake execution identity rather than replacing identity-bound fields.
    from pixelgym.grounding.v5.contracts import PolicyManifest

    fields = dict(manifest.__dict__)
    fields.pop("policy_id")
    fields.update(
        provider="in-process-fake",
        model="no-cost-scripted-policy-v1",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=5.0,
        cancellation_mode="none",
        reconciliation_deadline_seconds=0.0,
        dirty_worktree_policy="reject",
    )
    manifest = PolicyManifest.build(**fields)
    summary["binding"]["v5_policy_id"] = manifest.policy_id
    package = StatefulPolicyPackage.build(
        manifest=manifest,
        model_alias_disclosure=None,
        package_source_sha256=package.package_source_sha256,
        dependency_lock_sha256=package.dependency_lock_sha256,
        max_steps=package.max_steps,
        evidence_class=package.evidence_class,
        evidence=dataclasses.replace(package.evidence, summary_sha256=content_digest(summary)),
        code_revision=manifest.code_revision,
        code_state="clean",
        source_tree_sha256=package.source_tree_sha256,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )
    return package, summary


@pytest.mark.parametrize("role", ["A", "B"])
def test_exact_thresholds_and_separate_episode_denominators(role: str) -> None:
    package, summary = candidate(role)
    report = evaluate(package, summary)
    validate_report_rules(report)
    assert report.overall_passed
    assert package.evidence.exact_successes == int(role == "B")
    assert report.gate_policy_sha256 == content_digest(policy_document())[7:]


@pytest.mark.parametrize("name", [r[0] for r in RULES])
def test_every_missing_quantity_fails_closed(name: str) -> None:
    package, summary = candidate(**{name: None})
    report = evaluate(package, summary)
    assert not report.overall_passed
    assert name in report.reasons


@pytest.mark.parametrize("value", [True, "0", float("nan"), float("inf"), -1])
def test_invalid_quantities_are_not_validated_as_zero(value: Any, monkeypatch: Any) -> None:
    package, summary = candidate()
    summary["metrics"]["real_provider_requests"] = value
    # Nonfinite input cannot have a canonical JSON digest. Isolate numeric validation
    # after the identity check so the evaluator itself must reject it.
    monkeypatch.setattr(
        "pixelgym.platform.s7_policy.content_digest", lambda _: package.evidence.summary_sha256
    )
    with pytest.raises((ValueError, TypeError)):
        evaluate(package, summary)


@pytest.mark.parametrize("field", ["gate_policy_version", "gate_policy_sha256", "observations"])
def test_report_cannot_substitute_policy_or_drop_rules(field: str) -> None:
    package, summary = candidate()
    report = evaluate(package, summary)
    changes = {
        field: report.observations[:-1]
        if field == "observations"
        else ("f" * 64 if field == "gate_policy_sha256" else "other-policy")
    }
    with pytest.raises(ValueError, match="S7 gate"):
        validate_report_rules(dataclasses.replace(report, **changes))


def test_changed_comparator_and_threshold_are_refused() -> None:
    package, summary = candidate()
    report = evaluate(package, summary)
    item = dataclasses.replace(report.observations[0], comparator="at_most", threshold=2)
    with pytest.raises(ValueError, match="exact approved rules"):
        validate_report_rules(
            dataclasses.replace(report, observations=(item, *report.observations[1:]))
        )


def test_summary_identity_and_unknown_observations_are_refused() -> None:
    package, summary = candidate()
    summary["binding"]["run_reference"] = "other"
    with pytest.raises(ValueError, match="digest"):
        evaluate(package, summary)
    package, summary = candidate(unknown_quantity=0)
    with pytest.raises(ValueError, match="unsupported S7 observation"):
        evaluate(package, summary)


def test_unconfigured_s7_verifier_blocks_registration_even_with_policy_digest(
    tmp_path: Path,
) -> None:
    package, summary = candidate()
    control = ControlStore(
        tmp_path / "control.db", approved_stateful_gate_policy_sha256s=(POLICY_SHA256,)
    )
    try:
        control.migrate()
        with pytest.raises(TransitionError, match="stored-evidence gate verifier"):
            control.register_stateful_candidate(
                package=package,
                gate_report=evaluate(package, summary),
                artifacts=bound_refs(package),
            )
        assert control.active()[0] is None
    finally:
        control.connection.close()


def test_budget_survives_reopen_and_refuses_duplicate_reservations(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite"
    budget = S7SendBudget(path)
    for index in range(24):
        budget.reserve("B", str(index))
    reopened = S7SendBudget(path)
    assert reopened.counts() == {"B": 24}
    with pytest.raises(S7CapReached):
        reopened.reserve("B", "overflow")
    with pytest.raises(S7CapReached):
        reopened.reserve("A", "0")
    assert reopened.counts() == {"B": 24}


def test_budget_serializes_concurrent_last_slot(tmp_path: Path) -> None:
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    for index in range(3):
        budget.reserve("A", str(index))

    def reserve(index: int) -> bool:
        try:
            budget.reserve("A", f"last-{index}")
            return True
        except S7CapReached:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    assert budget.counts() == {"A": 4}


def test_every_phase_cap_and_total_cap_are_immutable(tmp_path: Path) -> None:
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    for phase, cap in policy_document()["phase_caps"].items():
        for index in range(cap):
            budget.reserve(phase, f"{phase}-{index}")
        with pytest.raises(S7CapReached):
            budget.reserve(phase, phase + "-extra")
    assert sum(budget.counts().values()) == 111
    with sqlite3.connect(budget.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM sends")


def test_wall_limit_and_forbidden_controls_do_not_send(tmp_path: Path, monkeypatch: Any) -> None:
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    transport = BudgetedScriptedTransport(budget, phase="qualification")
    with pytest.raises(S7CapReached):
        transport.cancel(idempotency_key="a", mode="none")
    with pytest.raises(S7CapReached):
        transport.reconcile(idempotency_key="a", deadline_seconds=0)
    monkeypatch.setattr("pixelgym.platform.s7_budget.time.time", lambda: 1e12)
    with pytest.raises(S7CapReached, match="wall-time"):
        transport.send({"scripted_action": {}}, idempotency_key="a", deadline_seconds=5)
    assert transport.model_requests == []
    assert budget.counts() == {}


def test_reservation_is_retained_when_fake_send_fails(tmp_path: Path) -> None:
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    transport = BudgetedScriptedTransport(budget, phase="qualification")
    with pytest.raises(KeyError):
        transport.send({}, idempotency_key="unknown", deadline_seconds=5)
    assert budget.counts() == {"qualification": 1}
    with pytest.raises(S7CapReached):
        transport.send({}, idempotency_key="unknown", deadline_seconds=5)


def test_source_and_factory_action_digests_detect_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "worker.py").write_bytes(b"original")
    from pixelgym.grounding.v5.retention import _hash

    binding = {"files": {"worker.py": _hash(source / "worker.py")}}
    verify_snapshot(source, binding)
    a = worker_binding([{"action_type": 0}], binding, {"python": "3.12"})
    b = worker_binding([{"action_type": 1}], binding, {"python": "3.12"})
    assert content_digest(a) != content_digest(b)
    (source / "worker.py").write_bytes(b"modified")
    with pytest.raises(ValueError, match="source"):
        verify_snapshot(source, binding)


def test_canonical_json_rejects_duplicate_fields_and_nonfinite_values(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    for data in (b'{"x":1,"x":2}', b'{"x":NaN}'):
        path.write_bytes(data)
        with pytest.raises(ValueError):
            read_json(path)


def test_seal_survives_sqlite_backup_normalization_and_detects_change(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    with sqlite3.connect(run / "journal.sqlite") as db:
        db.execute("CREATE TABLE evidence(value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('retained')")
    seal(run)
    before = inventory(run)
    archived = create_archive(run, tmp_path / "archive")
    restored = tmp_path / "restored"
    restore_archive(tmp_path / "archive", restored, expected_digest=archived["manifest_digest"])
    verify_seal(restored)
    assert inventory(restored) == before
    with sqlite3.connect(restored / "journal.sqlite") as db:
        db.execute("UPDATE evidence SET value = 'tampered'")
    with pytest.raises(ValueError, match="sealed evidence"):
        verify_seal(restored)


def test_report_does_not_execute_policy_replay_or_transport(
    tmp_path: Path, monkeypatch: Any
) -> None:
    package, summary = candidate()
    run = tmp_path / "run"
    run.mkdir()
    write_json(run / "plan.json", {"cumulative_accounting": None})
    for role in ("A", "B"):
        package, summary = candidate(role)
        write_json(run / f"packages/{role}/package.json", package.to_dict())
        write_json(run / f"packages/{role}/gate-report.json", evaluate(package, summary).to_dict())
    write_json(
        run / "results.json",
        {
            "fake_sends": {"qualification": 77},
            "real_provider_requests": 0,
            "activation_performed": False,
            "publication_performed": False,
        },
    )
    seal(run)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("report attempted execution")

    for name in (
        "replay_case",
        "inspect_qualification",
        "evaluate",
        "PixelGuiEnv",
        "V5FakeBackend",
    ):
        monkeypatch.setattr("pixelgym.platform.s7_evidence." + name, forbidden)
    monkeypatch.setattr("pixelgym.platform.s7_qualification.SandboxedPolicyProcess", forbidden)
    monkeypatch.setattr("pixelgym.platform.s7_qualification.BudgetedScriptedTransport", forbidden)
    for target in (
        "pixelgym.platform.policy_subprocess.SandboxedPolicyProcess",
        "pixelgym.platform.s7_budget.BudgetedScriptedTransport",
        "pixelgym.grounding.v5.runner.ScriptedTransport",
        "pixelgym.scripted_policy.ScriptedStatefulPolicy",
        "pixelgym.platform.stateful_runtime.create_serving_app",
        "pixelgym.platform.stateful_service.create_episode_router",
        "pixelgym.platform.serving_episode.ServingEpisodeHost",
    ):
        monkeypatch.setattr(target, forbidden)
    before = inventory(run)
    with no_network():
        report = stored_report(run)
        with pytest.raises(RuntimeError, match="network sockets"):
            socket.socket()
    assert report["activation_performed"] is False
    assert report["milestone_verdict"] is None
    assert inventory(run) == before


def test_unsealed_or_extra_evidence_is_refused(tmp_path: Path) -> None:
    write_json(tmp_path / "payload.json", {"evidence": "fixture"})
    seal(tmp_path)
    (tmp_path / "extra.txt").write_text("unlisted")
    with pytest.raises(ValueError, match="sealed evidence"):
        stored_report(tmp_path)


def test_stopped_report_retains_partial_observations_without_generating_packages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from pixelgym.platform.s7_evidence import render_report
    from pixelgym.platform.s7_source import content_digest_bytes

    write_json(tmp_path / "source/fixture.json", {})
    write_json(
        tmp_path / "source-binding.json",
        {
            "revision": "3" * 40,
            "files": {
                "fixture.json": content_digest_bytes(
                    (tmp_path / "source/fixture.json").read_bytes()
                )
            },
        },
    )
    write_json(
        tmp_path / "plan.json",
        {
            "schema_version": "pixelgym-s7-qualification-plan-v1",
            "evidence_label": EVIDENCE_LABEL,
            "gate_policy_sha256": POLICY_SHA256,
        },
    )
    budget = S7SendBudget(tmp_path / "send-budget.sqlite")
    for i in range(73):
        budget.reserve("qualification", str(i))
    write_json(
        tmp_path / "failure.json",
        {
            "qualification_complete": False,
            "activation_performed": False,
            "publication_performed": False,
            "source_revision": "3" * 40,
            "stage": "malformed_parse",
            "cause": "parser RPC rejection",
            "fake_send_reservations": 73,
            "real_provider_requests": 0,
            "monetary_spend_usd": 0,
        },
    )
    write_json(
        tmp_path / "shared/cases/golden/observation.json",
        {
            "case": "golden",
            "episode": {"classification": "success_termination"},
            "scripted_sends": 20,
        },
    )
    seal(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("stopped report attempted execution")

    for name in ("inspect_qualification", "evaluate", "build_packages"):
        monkeypatch.setattr("pixelgym.platform.s7_evidence." + name, forbidden)
    before = inventory(tmp_path)
    with no_network():
        report = stored_report(tmp_path)
        rendered = render_report(report)
    assert report["qualification_complete"] is False
    assert report["fake_sends"] == {"qualification": 73}
    assert "golden | success_termination | 20" in rendered
    assert "no package or gate report" in rendered
    assert inventory(tmp_path) == before

    (tmp_path / "packages").mkdir()
    with pytest.raises(ValueError, match="conflicting completion"):
        stored_report(tmp_path)


def stopped_archive(root: Path, count: int) -> Path:
    """Explicit unit predecessor with durable reservations and no transport invocation."""
    from pixelgym.platform.s7_source import content_digest_bytes

    run = root / "stopped"
    write_json(run / "source/fixture.json", {"fixture": "retained source"})
    write_json(
        run / "source-binding.json",
        {
            "revision": "3" * 40,
            "files": {
                "fixture.json": content_digest_bytes((run / "source/fixture.json").read_bytes())
            },
        },
    )
    write_json(
        run / "plan.json",
        {
            "fixture": str(count),
            "schema_version": "pixelgym-s7-qualification-plan-v1",
            "evidence_label": EVIDENCE_LABEL,
            "gate_policy_sha256": POLICY_SHA256,
        },
    )
    write_json(
        run / "failure.json",
        {
            "qualification_complete": False,
            "activation_performed": False,
            "publication_performed": False,
            "real_provider_requests": 0,
            "monetary_spend_usd": 0,
            "fake_send_reservations": count,
        },
    )
    budget = S7SendBudget(run / "send-budget.sqlite")
    for index in range(count):
        budget.reserve("qualification", str(index))
    seal(run)
    archive = root / "archive"
    create_archive(run, archive)
    return archive


def test_predecessors_preserve_consumed_allowance_and_refuse_duplicates_or_changed_counts(
    tmp_path: Path,
) -> None:
    from pixelgym.platform.s7_predecessors import freeze_predecessors, verify_predecessors

    archive = stopped_archive(tmp_path / "first", 73)
    run = tmp_path / "replacement"
    accounting = freeze_predecessors(run, (approved_archive(archive),), expected_sends=73)
    assert verify_predecessors(run, accounting) == 73
    assert accounting["qualification_allowance"] == 150
    assert accounting["campaign_allowance"] == 184
    original = inventory(run)
    changed = json.loads(json.dumps(accounting))
    changed["records"][0]["qualification_sends"] = 0
    with pytest.raises(ValueError, match="count changed"):
        verify_predecessors(run, changed)
    duplicate_run = tmp_path / "duplicate"
    with pytest.raises(ValueError, match="identity"):
        freeze_predecessors(
            duplicate_run,
            (approved_archive(archive), approved_archive(archive)),
            expected_sends=146,
        )
    assert inventory(run) == original


def completed_archive(root: Path, count: int, *, nested: bool, name: str = "done") -> Path:
    """Explicit completed unit predecessor: sealed results and packages, no replay."""
    from pixelgym.platform.s7_source import content_digest_bytes

    run = root / name / "qualification" if nested else root / name
    write_json(run / "source/fixture.json", {"fixture": "completed source " + name})
    write_json(
        run / "source-binding.json",
        {
            "revision": "4" * 40,
            "files": {
                "fixture.json": content_digest_bytes((run / "source/fixture.json").read_bytes())
            },
        },
    )
    plan = {
        "fixture": name + str(count),
        "schema_version": "pixelgym-s7-qualification-plan-v1",
        "evidence_label": EVIDENCE_LABEL,
        "gate_policy_sha256": POLICY_SHA256,
    }
    write_json(run / "plan.json", plan)
    write_json(
        run / "results.json",
        {
            "schema_version": "pixelgym-s7-qualification-results-v1",
            "activation_performed": False,
            "publication_performed": False,
            "real_provider_requests": 0,
            "provider_control_requests": 0,
            "monetary_spend_usd": 0,
            "plan_sha256": content_digest(plan),
            "fake_sends": {"qualification": count},
        },
    )
    write_json(run / "verification.json", {"fixture": "stored verification"})
    write_json(run / "packages/A.json", {"fixture": "package"})
    budget = S7SendBudget(run / "send-budget.sqlite")
    for index in range(count):
        budget.reserve("qualification", str(index))
    seal(run)
    if nested:
        write_json(root / name / "review/report.json", {"fixture": "review"})
    archive = root / (name + "-archive")
    create_archive(root / name, archive)
    return archive


def test_completed_and_stopped_predecessors_accumulate_into_one_allowance(
    tmp_path: Path,
) -> None:
    from pixelgym.platform.s7_predecessors import freeze_predecessors, verify_predecessors

    stopped = stopped_archive(tmp_path / "stopped", 73)
    completed = completed_archive(tmp_path, 77, nested=True)
    run = tmp_path / "successor"
    accounting = freeze_predecessors(
        run, (approved_archive(stopped), approved_archive(completed)), expected_sends=150
    )
    assert [r["qualification_sends"] for r in accounting["records"]] == [73, 77]
    assert accounting["records"][0]["path"] == "predecessors/0/run"
    assert accounting["records"][1]["path"] == "predecessors/1/run/qualification"
    assert accounting["qualification_allowance"] == 227
    assert accounting["campaign_allowance"] == 261
    assert verify_predecessors(run, accounting) == 150
    assert (run / "predecessors/1/run/review/report.json").exists()
    flat = completed_archive(tmp_path / "flat", 5, nested=False)
    flat_run = tmp_path / "flat-successor"
    flat_accounting = freeze_predecessors(flat_run, (approved_archive(flat),), expected_sends=5)
    assert flat_accounting["records"][0]["path"] == "predecessors/0/run"
    assert verify_predecessors(flat_run, flat_accounting) == 5


@pytest.mark.parametrize(
    ("damage", "match"),
    [
        ("failure", "retains a failure record"),
        ("packages", "lacks packages"),
        ("plan", "different plan"),
        ("ledger", "durable ledger"),
        ("spend", "zero-spend"),
        ("neither", "neither a failure record"),
    ],
)
def test_completed_predecessor_refuses_conflicting_or_unbound_evidence(
    tmp_path: Path, damage: str, match: str
) -> None:
    import shutil

    from pixelgym.platform.s7_predecessors import completed_count, predecessor_count

    completed_archive(tmp_path, 3, nested=False)
    run = tmp_path / "done"
    (run / "seal.json").unlink()

    def rewrite(name: str, value: dict[str, Any]) -> None:
        (run / name).unlink()
        write_json(run / name, value)

    if damage == "failure":
        write_json(run / "failure.json", {"stage": "qualification"})
    elif damage == "packages":
        shutil.rmtree(run / "packages")
    elif damage == "plan":
        rewrite("plan.json", {**read_json(run / "plan.json"), "fixture": "edited"})
    elif damage == "ledger":
        S7SendBudget(run / "send-budget.sqlite").reserve("qualification", "extra")
    elif damage == "spend":
        rewrite("results.json", {**read_json(run / "results.json"), "monetary_spend_usd": 1})
    else:
        (run / "results.json").unlink()
    seal(run)
    # A failure record routes through the stopped path; completed_count itself must refuse it.
    count = completed_count if damage == "failure" else predecessor_count
    with pytest.raises(ValueError, match=match):
        count(run)


def test_predecessor_root_requires_a_seal(tmp_path: Path) -> None:
    from pixelgym.platform.s7_predecessors import predecessor_root

    (tmp_path / "qualification").mkdir()
    with pytest.raises(ValueError, match="no sealed qualification"):
        predecessor_root(tmp_path)


def test_git_source_refuses_modified_untracked_ignored_and_wrong_root(tmp_path: Path) -> None:
    from pixelgym.platform.s7_source import git_source, snapshot

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pixelgym").mkdir()
    (root / "pixelgym/worker.py").write_text("frozen source\n")
    for name in (
        "scripts/tool.py",
        "requirements/runtime.lock",
        "pyproject.toml",
        "docs/s7-serving-demo-proposal.md",
    ):
        path = root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("fixture\n")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "fixture",
    )
    revision, files = git_source(root)
    assert len(revision) == 40 and files["pixelgym/worker.py"] == b"frozen source\n"
    binding = snapshot(root, tmp_path / "snapshot")
    verify_snapshot(tmp_path / "snapshot", binding)
    (root / "pixelgym/worker.py").write_text("modified source\n")
    with pytest.raises(ValueError, match="clean"):
        git_source(root)
    git("restore", "pixelgym/worker.py")
    (root / "pixelgym/extra.py").write_text("untracked")
    with pytest.raises(ValueError, match="untracked"):
        git_source(root)
    (root / ".git/info/exclude").write_text("pixelgym/extra.py\n")
    with pytest.raises(ValueError, match="ignored"):
        git_source(root)
    with pytest.raises(ValueError, match="exact Git checkout"):
        git_source(root / "pixelgym")


def test_runtime_lock_and_worker_factory_are_checked_before_launch(monkeypatch: Any) -> None:
    from pixelgym.platform.s7_source import verify_runtime, worker_spec

    monkeypatch.setattr("pixelgym.platform.s7_source.runtime_lock", lambda: {"python": "3.12"})
    verify_runtime({"python": "3.12"})
    with pytest.raises(ValueError, match="installed runtime"):
        verify_runtime({"python": "3.13"})
    binding = worker_binding([], {}, {})
    binding["factory_name"] = "UnapprovedPolicy"
    with pytest.raises(ValueError, match="approved scripted factory"):
        worker_spec(Path.cwd(), Path("/tmp/private-s7-evidence"), binding)


def test_review_wait_preserves_caps_and_does_not_allow_sends(
    tmp_path: Path, monkeypatch: Any
) -> None:
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    for index in range(77):
        budget.reserve("qualification", str(index))
    budget.pause_for_owner_review()
    with pytest.raises(S7CapReached, match="owner review"):
        budget.reserve("A", "a")
    # Human review time is not active execution time, but no cap is reset.
    monkeypatch.setattr("pixelgym.platform.s7_budget.time.time", lambda: 1e12)
    budget.resume_after_owner_review("sha256:" + "a" * 64)
    budget.reserve("A", "a")
    assert budget.counts() == {"qualification": 77, "A": 1}
    with pytest.raises(S7CapReached):
        budget.reserve("qualification", "extra")


def qualification_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    for name in (
        "pixelgym/fixture.py",
        "scripts/fixture.py",
        "requirements/runtime.lock",
        "pyproject.toml",
        "docs/s7-serving-demo-proposal.md",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    for args in (
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "fixture",
        ),
    ):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    return root


@pytest.mark.parametrize("proof_name", ["worker.json", "recovery-worker.json"])
def test_worker_proof_write_failure_closes_every_worker_without_sending(
    tmp_path: Path, monkeypatch: Any, proof_name: str
) -> None:
    from types import SimpleNamespace

    from pixelgym.grounding.v5.runner import InjectedInterruption, ScriptedStatefulPolicy
    from pixelgym.platform.s7_qualification import prepare, qualify

    root, run = qualification_checkout(tmp_path), tmp_path / "failed-proof"
    fixture_host_source(monkeypatch)
    prepare(
        root,
        run,
        run_id="cleanup-unit-fixture",
        approval="unit fixture only",
        expected_predecessor_sends=0,
    )
    workers = []

    class FixtureWorker(ScriptedStatefulPolicy):
        os_sandbox_applied = False
        sandbox_mechanism = "unit_fixture_unenforced"
        sandbox_profile_digest = None

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(())
            workers.append(self)

    class NoSendRunner:
        """Reach the proof-write boundaries without executing any policy or transport."""

        def __init__(self, **kwargs: Any) -> None:
            self.interruption = kwargs.get("interrupt_after")
            self.directory = kwargs["journal"].path.parent
            if (
                proof_name == "recovery-worker.json"
                and self.directory.name == "resume"
                and not self.interruption
            ):
                assert (self.directory / "recovery-worker.json").exists()

        def run(self, **kwargs: Any) -> Any:
            assert (self.directory / "worker.json").exists(), "proof must precede execution"
            if self.interruption:
                raise InjectedInterruption(self.interruption)
            return SimpleNamespace(to_dict=dict)

    def write_or_fail(path: Path, value: Any) -> None:
        if path.name == proof_name:
            raise OSError("fixture proof storage unavailable")
        write_json(path, value)

    monkeypatch.setattr("pixelgym.platform.s7_qualification.SandboxedPolicyProcess", FixtureWorker)
    monkeypatch.setattr("pixelgym.platform.s7_qualification.V5Runner", NoSendRunner)
    monkeypatch.setattr("pixelgym.platform.s7_qualification.write_json", write_or_fail)
    with no_network(), pytest.raises(OSError, match="proof storage unavailable"):
        qualify(root, run)
    assert len(workers) == (1 if proof_name == "worker.json" else 8)
    assert all(worker.closed for worker in workers)
    assert S7SendBudget(run / "send-budget.sqlite").counts() == {}


def test_full_scripted_driver_counts_and_cleanup_but_refuses_unenforced_proof(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from pixelgym.grounding.v5.retention_validation import replay_campaign
    from pixelgym.grounding.v5.runner import ScriptedStatefulPolicy
    from pixelgym.platform.s7_evidence import inspect_qualification
    from pixelgym.platform.s7_qualification import prepare, qualify

    root = qualification_checkout(tmp_path)
    fixture_host_source(monkeypatch)
    workers = []

    class UnenforcedFixtureWorker(ScriptedStatefulPolicy):
        os_sandbox_applied = False
        sandbox_mechanism = "unit_fixture_unenforced"
        sandbox_profile_digest = None

        def __init__(self, *, spec: Any, require_os_sandbox: bool) -> None:
            assert require_os_sandbox
            super().__init__(tuple(json.loads(spec.factory_kwargs_json)["actions"]))
            workers.append(self)

    monkeypatch.setattr(
        "pixelgym.platform.s7_qualification.SandboxedPolicyProcess", UnenforcedFixtureWorker
    )
    run = tmp_path / "run"
    prepare(
        root,
        run,
        run_id="unit-fixture",
        approval="unit fixture only",
        predecessor_archives=(approved_archive(stopped_archive(tmp_path / "predecessor", 73)),),
        expected_predecessor_sends=73,
    )
    with no_network():
        result = qualify(root, run)
        replay = replay_campaign(run / "shared")
    assert result["fake_sends"] == {"qualification": 77}
    assert result["baseline"]["episode"]["classification"] == "pilot_action_limit"
    assert replay["case_count"] == 7
    assert len(workers) == 9 and all(worker.closed for worker in workers)
    with pytest.raises(ValueError, match="enforced worker sandbox"):
        inspect_qualification(run)
    with pytest.raises(ValueError, match="already started"):
        qualify(root, run)

    # Synthetic declarations below exercise the stored-format pipeline only. They are not
    # OS-isolation evidence; the real Darwin integration test and actual qualification use
    # the real launcher, without this fixture or metadata substitution.
    from pixelgym.platform.s7_evidence import build_packages, store_artifacts, verify_packages
    from pixelgym.serialization import canonical_json_bytes

    for worker in result["workers"]:
        worker.update(
            os_sandbox_applied=True,
            mechanism="darwin_sandbox_exec",
            profile_sha256="sha256:" + "a" * 64,
        )
    (run / "results.json").write_bytes(canonical_json_bytes(result))
    verification = inspect_qualification(run)
    assert verification["cumulative_qualification_sends"] == 150
    packages = build_packages(run, verification)
    assert all(p["gate_overall_passed"] for p in packages.values())
    store_artifacts(run)
    seal(run)
    verify_packages(run)
    assert stored_report(run)["fake_sends"] == {"qualification": 77}
    original = inventory(run)
    archive = create_archive(run, tmp_path / "archive")
    restored = tmp_path / "restored"
    restore_archive(tmp_path / "archive", restored, expected_digest=archive["manifest_digest"])
    verify_seal(restored)
    verify_packages(restored)
    assert inventory(restored) == original


def test_s7_loader_refuses_an_unverified_source_before_worker_start() -> None:
    from pixelgym.platform.s7_source import worker_spec
    from pixelgym.platform.stateful_runtime import StatefulLoadError, WorkerSpecPolicyLoader

    package, _ = candidate()
    binding = worker_binding([], {}, {})
    spec = worker_spec(Path.cwd(), Path("/tmp/private-s7-evidence"), binding)
    loader = WorkerSpecPolicyLoader(
        worker_specs={package.package_source_sha256: spec}, transport_factory=lambda package: None
    )  # type: ignore[arg-type]
    with pytest.raises(StatefulLoadError, match="verified worker source binding"):
        loader.load(package)


def test_scripted_worker_reuses_runner_policy_without_importing_host_journal() -> None:
    import sys

    from pixelgym.grounding.v5.runner import ScriptedStatefulPolicy as runner_policy
    from pixelgym.scripted_policy import ScriptedStatefulPolicy

    assert runner_policy is ScriptedStatefulPolicy
    program = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from pixelgym.scripted_policy import ScriptedStatefulPolicy; "
        "assert 'sqlite3' not in sys.modules; assert 'numpy' not in sys.modules; "
        "assert 'pixelgym.grounding.v5.runner' not in sys.modules; "
        "p=ScriptedStatefulPolicy(()); p.reset('import-only fixture'); p.close()"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(Path.cwd())],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
