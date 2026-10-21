"""Offline regression evidence for PR252 review; no retained campaign is executed."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.retention import create_archive
from pixelgym.grounding.v5.runner import ScriptedStatefulPolicy
from pixelgym.platform.control_store import ControlStore, TransitionError
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.s7_budget import S7CapReached, S7SendBudget
from pixelgym.platform.s7_evidence import (
    gate_verifier,
    inventory,
    render_report,
    stored_report,
    validate_recorded_execution,
    verify_seal,
)
from pixelgym.platform.s7_policy import POLICY_SHA256, evaluate
from pixelgym.platform.s7_predecessors import freeze_predecessors, stopped_count
from pixelgym.platform.s7_qualification import prepare, qualify, read_json, seal
from pixelgym.platform.s7_source import verify_host_source, write_json
from pixelgym.platform.stateful_control import StatefulGateReport, stateful_package_from_dict
from scripts import s7_serving
from tests.unit.platform.test_s7_serving import (
    approved_archive,
    bound_refs,
    candidate,
    fixture_host_source,
    qualification_checkout,
    stopped_archive,
)


@contextmanager
def control_context(control: ControlStore) -> Iterator[ControlStore]:
    try:
        yield control
    finally:
        control.connection.close()


def test_configured_gate_verifier_refuses_registration_and_rechecks_before_approval(
    tmp_path: Path,
) -> None:
    package, summary = candidate()
    report = evaluate(package, summary)
    calls = []
    reject = True

    def verifier(*args: Any) -> None:
        calls.append(args)
        if reject:
            raise ValueError("fixture rejected stored derivation")

    with control_context(
        ControlStore(
            tmp_path / "control.db",
            reviewer_identity="owner",
            approved_stateful_gate_policy_sha256s=(POLICY_SHA256,),
            stateful_gate_verifiers={POLICY_SHA256: verifier},
        )
    ) as control:
        control.migrate()
        with pytest.raises(ValueError, match="fixture rejected"):
            control.register_stateful_candidate(
                package=package, gate_report=report, artifacts=bound_refs(package)
            )
        assert control.list_stateful_candidates() == []
        reject = False
        registered = control.register_stateful_candidate(
            package=package, gate_report=report, artifacts=bound_refs(package)
        )
        assert len(calls) == 2
        reject = True
        with pytest.raises(ValueError, match="fixture rejected"):
            control.approve(
                registered.candidate_id,
                actor="owner",
                reason="unit fixture",
                gate_report_sha256=registered.gate_report_sha256,
            )
        assert len(calls) == 3
        assert control.get_stateful_candidate(registered.candidate_id).state.value == "Eligible"
        assert control.active()[0] is None


@pytest.mark.parametrize("role,cap", [("A", 4), ("B", 24)])
@pytest.mark.parametrize("s7", [True, False])
def test_serving_caps_on_record_and_reverification(
    tmp_path: Path, role: str, cap: int, s7: bool
) -> None:
    from tests.unit.platform.stateful_fixtures import GATE_POLICY_SHA256, gate_report

    package, summary = candidate(role)
    report = evaluate(package, summary) if s7 else gate_report(package)
    with control_context(
        ControlStore(
            tmp_path / "control.db",
            reviewer_identity="owner",
            approved_stateful_gate_policy_sha256s=(POLICY_SHA256, GATE_POLICY_SHA256),
            stateful_gate_verifiers={POLICY_SHA256: lambda *args: None},
        )
    ) as control:
        control.migrate()
        registered = control.register_stateful_candidate(
            package=package, gate_report=report, artifacts=bound_refs(package)
        )
        control.approve(
            registered.candidate_id,
            actor="owner",
            reason="unit fixture",
            gate_report_sha256=registered.gate_report_sha256,
        )

        def terms(count: int) -> Any:
            return control.approve_stateful_serving_terms(
                registered.candidate_id,
                actor="owner",
                reason="fixture terms",
                deployment_attempt_cap=count,
                deployment_tier="demo",
            )

        if s7:
            with pytest.raises(TransitionError, match="exact-version cap"):
                terms(cap + 1)
            assert (
                control.connection.execute(
                    "SELECT count(*) FROM stateful_serving_terms"
                ).fetchone()[0]
                == 0
            )
        else:
            cap += 1
        assert terms(cap).deployment_attempt_cap == cap
        assert (
            control.verify_stateful_candidate_approval(registered.candidate_id)[
                2
            ].deployment_attempt_cap
            == cap
        )
        # Simulate externally corrupted storage after proving the append-only guard.
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            control.connection.execute(
                "UPDATE stateful_serving_terms SET deployment_attempt_cap = ?", (cap + 1,)
            )
        for row in control.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name = 'stateful_serving_terms'"
        ).fetchall():
            control.connection.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
        control.connection.execute(
            "UPDATE stateful_serving_terms SET deployment_attempt_cap = ?", (cap + 1,)
        )
        control.connection.commit()
        if s7:
            with pytest.raises(TransitionError, match="exact-version cap"):
                control.verify_stateful_candidate_approval(registered.candidate_id)
        else:
            assert (
                control.verify_stateful_candidate_approval(registered.candidate_id)[
                    2
                ].deployment_attempt_cap
                == cap + 1
            )
        assert control.active()[0] is None


def test_unknown_s7_package_shape_has_no_implicit_cap() -> None:
    with pytest.raises(TransitionError, match="exact-version cap"):
        ControlStore._verify_s7_serving_cap(
            {"gate_report_json": json.dumps({"gate_policy_sha256": POLICY_SHA256})},
            SimpleNamespace(max_steps=2),
            24,
        )  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def qualified_fixture(tmp_path_factory: Any) -> Path:
    """Synthetic OS proof exercises stored formats only; never evidence of OS enforcement."""
    directory = tmp_path_factory.mktemp("s7-review-unit")
    root = qualification_checkout(directory)
    run = directory / "run"

    class FixtureWorker(ScriptedStatefulPolicy):
        os_sandbox_applied = False
        sandbox_mechanism = "unit_fixture_unenforced"
        sandbox_profile_digest = None

        def __init__(self, *, spec: Any, **kwargs: Any) -> None:
            super().__init__(tuple(json.loads(spec.factory_kwargs_json)["actions"]))

    with pytest.MonkeyPatch.context() as patch:
        fixture_host_source(patch)
        patch.setattr("pixelgym.platform.s7_qualification.SandboxedPolicyProcess", FixtureWorker)
        prepare(
            root,
            run,
            run_id="review-unit-fixture",
            approval="unit fixture only",
            expected_predecessor_sends=0,
        )
        with s7_serving.no_network():
            results = qualify(root, run)
        for worker in results["workers"]:
            worker.update(
                os_sandbox_applied=True,
                mechanism="darwin_sandbox_exec",
                profile_sha256="sha256:" + "a" * 64,
            )
        from pixelgym.serialization import canonical_json_bytes

        (run / "results.json").write_bytes(canonical_json_bytes(results))
        s7_serving.finalize(run)
    return run


def bundle(run: Path, role: str = "B") -> tuple[Any, Any, tuple[Any, ...]]:
    from pixelgym.platform.contracts import ArtifactRef

    package = stateful_package_from_dict(read_json(run / f"packages/{role}/package.json"))
    report = StatefulGateReport.from_dict(read_json(run / f"packages/{role}/gate-report.json"))
    refs = tuple(ArtifactRef(**r) for r in read_json(run / "artifact-references.json")["artifacts"])
    return package, report, refs


@pytest.mark.parametrize(
    "missing",
    [
        "plan.json",
        "packages/B/summary.json",
        "results.json",
        "verification.json",
        "source-binding.json",
        "runtime-lock.json",
    ],
)
def test_gate_verifier_refuses_omitted_bound_documents(
    qualified_fixture: Path, missing: str
) -> None:
    run = qualified_fixture
    package, report, refs = bundle(run)
    refs = tuple(r for r in refs if r.logical_key != "s7/" + missing)
    with pytest.raises(ValueError, match="omit bound evidence"):
        gate_verifier(LocalImmutableStore(run / "immutable"), qualification_root=run)(
            package, report, refs
        )


def test_gate_verifier_rejects_forged_summary_metrics(
    qualified_fixture: Path, tmp_path: Path
) -> None:
    package, _, refs = bundle(qualified_fixture)
    summary = read_json(qualified_fixture / "packages/B/summary.json")
    summary["metrics"]["real_provider_requests"] = 1
    changed = dataclasses.asdict(package)
    # Rebuild the full identity through its contract, never bypass it with replace().
    from pixelgym.platform.stateful_contracts import StatefulPolicyPackage

    rebuilt = StatefulPolicyPackage.build(
        manifest=package.v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256=package.package_source_sha256,
        dependency_lock_sha256=package.dependency_lock_sha256,
        max_steps=package.max_steps,
        evidence_class=package.evidence_class,
        evidence=dataclasses.replace(package.evidence, summary_sha256=content_digest(summary)),
        code_revision=package.code_revision,
        code_state=package.code_state,
        source_tree_sha256=package.source_tree_sha256,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )
    assert changed["policy_id"] != rebuilt.policy_id
    from pixelgym.serialization import canonical_json_bytes

    store = LocalImmutableStore(tmp_path / "immutable")
    original = LocalImmutableStore(qualified_fixture / "immutable")
    copied = [
        store.put_once(str(i), original.get_verified(r), media_type="application/json")
        for i, r in enumerate(refs)
    ]
    copied.append(
        store.put_once(
            "forged-summary", canonical_json_bytes(summary), media_type="application/json"
        )
    )
    with pytest.raises(ValueError, match="does not follow its bound evidence"):
        gate_verifier(store, qualification_root=qualified_fixture)(
            rebuilt, evaluate(rebuilt, summary), tuple(copied)
        )


def test_gate_verifier_rejects_receipt_different_from_private_seal(
    qualified_fixture: Path, monkeypatch: Any
) -> None:
    package, report, refs = bundle(qualified_fixture)
    real_read = read_json

    def changed_read(path: Path) -> Any:
        value = real_read(path)
        if path.name == "results.json":
            value["fixture_tamper"] = True
        return value

    monkeypatch.setattr("pixelgym.platform.s7_evidence.read_json", changed_read)
    with pytest.raises(ValueError, match="immutable receipt differs"):
        gate_verifier(
            LocalImmutableStore(qualified_fixture / "immutable"),
            qualification_root=qualified_fixture,
        )(package, report, refs)


@pytest.mark.parametrize(
    "mutation",
    [
        "plan",
        "transport",
        "counts",
        "real",
        "control",
        "spend",
        "activation",
        "publication",
        "policy",
        "worker_allocation",
        "worker_enforcement",
    ],
)
def test_recorded_execution_rejects_tampering(qualified_fixture: Path, mutation: str) -> None:
    plan = read_json(qualified_fixture / "plan.json")
    results = read_json(qualified_fixture / "results.json")
    if mutation == "plan":
        results["plan_sha256"] = "sha256:" + "0" * 64
    elif mutation == "transport":
        results["transport"] = "UnbudgetedTransport"
    elif mutation == "counts":
        results["fake_sends"]["qualification"] -= 1
    elif mutation == "real":
        results["real_provider_requests"] = 1
    elif mutation == "control":
        results["provider_control_requests"] = 1
    elif mutation == "spend":
        results["monetary_spend_usd"] = 1
    elif mutation == "activation":
        results["activation_performed"] = True
    elif mutation == "publication":
        results["publication_performed"] = True
    elif mutation == "policy":
        plan["gate_policy_sha256"] = "0" * 64
    elif mutation == "worker_allocation":
        results["workers"].pop()
    else:
        results["workers"][0]["os_sandbox_applied"] = False
    with pytest.raises(ValueError, match="S7 recorded"):
        validate_recorded_execution(plan, results)


def test_cli_verify_and_report_preserve_evidence(
    qualified_fixture: Path, monkeypatch: Any, capsys: Any
) -> None:
    before = inventory(qualified_fixture)
    for command in ("verify", "report"):
        monkeypatch.setattr("sys.argv", ["s7_serving", command, "--run", str(qualified_fixture)])
        s7_serving.main()
        output = capsys.readouterr().out
        assert (
            "verification_sha256" if command == "verify" else "Code revision passed: True"
        ) in output
    assert inventory(qualified_fixture) == before
    observed = copy.deepcopy(read_json(qualified_fixture / "verification.json"))
    observed["fixture_tamper"] = True
    monkeypatch.setattr(s7_serving, "inspect_qualification", lambda _: observed)
    monkeypatch.setattr("sys.argv", ["s7_serving", "verify", "--run", str(qualified_fixture)])
    with pytest.raises(ValueError, match="stored semantic verification differs"):
        s7_serving.main()


def test_rendered_report_discloses_binding_and_observation_failures(
    qualified_fixture: Path,
) -> None:
    report = stored_report(qualified_fixture)
    gate = report["packages"]["B"]["gate"]
    gate.update(
        code_revision_passed=False,
        overall_passed=False,
        reasons=["clean bound source provenance is required"],
    )
    gate["observations"][0]["passed"] = False
    text = render_report(report)
    assert "Code revision passed: False" in text
    assert "Stored gate overall_passed: False" in text
    assert "clean bound source provenance is required" in text
    assert "| Passed |" in text and "| False |" in text
    assert "declared constants" in text


def test_predecessors_require_approved_digest_count_and_policy(tmp_path: Path) -> None:
    archive = stopped_archive(tmp_path / "first", 1)
    with pytest.raises(ValueError, match="digest"):
        freeze_predecessors(
            tmp_path / "wrong-digest", ((archive, "sha256:" + "0" * 64),), expected_sends=1
        )
    with pytest.raises(ValueError, match="explicit approved count"):
        freeze_predecessors(tmp_path / "omitted", (), expected_sends=1)
    with pytest.raises(ValueError, match="explicit approved count"):
        freeze_predecessors(
            tmp_path / "wrong-count", (approved_archive(archive),), expected_sends=0
        )
    run = tmp_path / "first/stopped"
    (run / "seal.json").unlink()
    plan = read_json(run / "plan.json")
    plan["gate_policy_sha256"] = "0" * 64
    (run / "plan.json").unlink()
    write_json(run / "plan.json", plan)
    seal(run)
    with pytest.raises(ValueError, match="approved gate policy"):
        stopped_count(run)


def test_stopped_reports_require_ledger_and_honest_count(tmp_path: Path) -> None:
    stopped_archive(tmp_path, 1)
    run = tmp_path / "stopped"
    (run / "seal.json").unlink()
    failure = read_json(run / "failure.json")
    failure["fake_send_reservations"] = 0
    (run / "failure.json").unlink()
    write_json(run / "failure.json", failure)
    seal(run)
    with pytest.raises(ValueError, match="durable ledger"):
        stored_report(run)
    (run / "seal.json").unlink()
    (run / "send-budget.sqlite").unlink()
    seal(run)
    with pytest.raises(sqlite3.OperationalError):
        stored_report(run)


def test_host_import_source_must_match_checkout(tmp_path: Path) -> None:
    import pixelgym

    verify_host_source(Path(pixelgym.__file__).resolve().parents[1])
    with pytest.raises(ValueError, match="imported host pixelgym"):
        verify_host_source(tmp_path)


def test_pause_resume_preserve_elapsed_and_reject_invalid_states(
    tmp_path: Path, monkeypatch: Any
) -> None:
    now = [100.0]
    monkeypatch.setattr("pixelgym.platform.s7_budget.time.time", lambda: now[0])
    budget = S7SendBudget(tmp_path / "budget.sqlite")
    with pytest.raises(ValueError, match="one complete qualification"):
        budget.pause_for_owner_review()
    with pytest.raises(ValueError, match="not awaiting"):
        budget.resume_after_owner_review("sha256:" + "a" * 64)
    for i in range(77):
        budget.reserve("qualification", str(i))
    now[0] = 1900.0 - 10
    budget.pause_for_owner_review()
    for invalid in ("", "approved", "sha256:bad"):
        with pytest.raises(ValueError, match="approval digest"):
            budget.resume_after_owner_review(invalid)
    now[0] = 100000.0
    budget.resume_after_owner_review("sha256:" + "a" * 64)
    now[0] += 10
    with pytest.raises(S7CapReached, match="wall-time"):
        budget.reserve("A", "after-time")
    with pytest.raises(ValueError, match="not awaiting"):
        budget.resume_after_owner_review("sha256:" + "a" * 64)


@pytest.mark.parametrize("partial_results", [False, True])
def test_qualification_failure_is_sealed_and_counted_without_retry(
    tmp_path: Path, monkeypatch: Any, partial_results: bool
) -> None:
    root = qualification_checkout(tmp_path)
    run = tmp_path / "failed"
    fixture_host_source(monkeypatch)
    prepare(
        root, run, run_id="failed-unit", approval="unit fixture only", expected_predecessor_sends=0
    )

    def fail(*args: Any) -> Any:
        budget = args[-1]
        budget.reserve("qualification", "reserved-before-fault")
        if partial_results:
            (run / "results.json").write_bytes(b'{"incomplete":')
        raise RuntimeError("private diagnostic is not copied")

    monkeypatch.setattr("pixelgym.platform.s7_qualification._qualify", fail)
    with pytest.raises(RuntimeError, match="private diagnostic"):
        qualify(root, run)
    verify_seal(run)
    assert stopped_count(run) == 1
    assert stored_report(run)["fake_sends"] == {"qualification": 1}
    assert read_json(run / "failure.json")["cause"] == "RuntimeError"
    with pytest.raises(ValueError, match="already started"):
        qualify(root, run)


@pytest.mark.parametrize(
    "fault", ["inspect_qualification", "build_packages", "store_artifacts", "_verify_packages"]
)
def test_cli_post_send_failures_remain_reportable_and_predecessor_eligible(
    tmp_path: Path, monkeypatch: Any, fault: str
) -> None:
    root = qualification_checkout(tmp_path)
    run = tmp_path / "failed-finalization"
    fixture_host_source(monkeypatch)
    prepare(
        root,
        run,
        run_id="finalization-unit",
        approval="unit fixture only",
        expected_predecessor_sends=0,
    )

    def execution(*args: Any) -> None:
        budget = S7SendBudget(run / "send-budget.sqlite")
        budget.reserve("qualification", "reserved")
        write_json(
            run / "results.json",
            {
                "fake_sends": budget.counts(),
                "plan_sha256": content_digest(read_json(run / "plan.json")),
            },
        )

    def build(*args: Any) -> dict[str, Any]:
        write_json(run / "verification.json", {"fixture": "partial finalization"})
        write_json(run / "packages/B/summary.json", {"fixture": "unusable partial package"})
        return {}

    def fail(*args: Any) -> Any:
        raise ValueError("fixture finalization refusal")

    monkeypatch.setattr(s7_serving, "qualify", execution)
    monkeypatch.setattr(s7_serving, "inspect_qualification", lambda _: {})
    monkeypatch.setattr(s7_serving, "build_packages", build)
    monkeypatch.setattr(s7_serving, "store_artifacts", lambda _: None)
    monkeypatch.setattr(s7_serving, "_verify_packages", lambda _: None)
    monkeypatch.setattr(s7_serving, fault, fail)
    monkeypatch.setattr(
        "sys.argv", ["s7_serving", "qualify", "--run", str(run), "--source", str(root)]
    )
    with pytest.raises(ValueError, match="finalization refusal"):
        s7_serving.main()
    verify_seal(run)
    assert stopped_count(run) == 1
    report = stored_report(run)
    assert report["qualification_complete"] is False
    assert report["fake_sends"] == {"qualification": 1}
    assert "usable for approval" in render_report(report)
    archive = tmp_path / "archive"
    receipt = create_archive(run, archive)
    accounting = freeze_predecessors(
        tmp_path / "successor", ((archive, receipt["manifest_digest"]),), expected_sends=1
    )
    assert accounting["previous_qualification_sends"] == 1


def test_s7_preparer_checks_source_before_smoke_and_load_and_counts_smoke(
    qualified_fixture: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    from pixelgym.platform.s7_runtime import preparer
    from pixelgym.platform.stateful_runtime import StatefulLoadError

    package, _, _ = bundle(qualified_fixture)
    budget = S7SendBudget(tmp_path / "runtime-budget.sqlite")
    prepared = preparer(
        root=tmp_path, qualification=qualified_fixture, state_root=tmp_path / "state", budget=budget
    )
    record = SimpleNamespace(package=package, candidate_id="unit-fixture")
    attempts = []
    refuse = True

    def source_check(*args: Any) -> None:
        attempts.append("source")
        if refuse:
            raise ValueError("fixture source mismatch")

    monkeypatch.setattr("pixelgym.platform.s7_runtime.verify_worker_binding", source_check)
    with pytest.raises(ValueError, match="source mismatch"):
        prepared.contract_smoke(record)
    assert budget.counts() == {}
    with pytest.raises(ValueError, match="source mismatch"):
        prepared.loader.load(package)
    assert attempts == ["source", "source"]
    foreign, _ = candidate()
    with pytest.raises(StatefulLoadError, match="outside the qualified exact bundle"):
        prepared.contract_smoke(SimpleNamespace(package=foreign))
    assert budget.counts() == {}

    refuse = False
    prepared.contract_smoke(record)
    assert budget.counts() == {"smoke": 1}

    # The actual loader must call the configured source check before spawning anything.
    def stop_before_spawn(*args: Any, **kwargs: Any) -> Any:
        assert attempts[-1] == "source"
        raise RuntimeError("fixture stops before worker spawn")

    monkeypatch.setattr(
        "pixelgym.platform.stateful_runtime.SandboxedPolicyProcess", stop_before_spawn
    )
    with pytest.raises(RuntimeError, match="before worker spawn"):
        prepared.loader.load(package)
    assert attempts == ["source"] * 4


def test_stopped_evidence_cannot_supply_gate_approval(
    qualified_fixture: Path, tmp_path: Path
) -> None:
    stopped_archive(tmp_path, 1)
    package, report, refs = bundle(qualified_fixture)
    with pytest.raises(ValueError, match="cannot supply approval evidence"):
        gate_verifier(
            LocalImmutableStore(qualified_fixture / "immutable"),
            qualification_root=tmp_path / "stopped",
        )(package, report, refs)


def test_cli_prepare_requires_explicit_predecessor_authority(
    tmp_path: Path, monkeypatch: Any
) -> None:
    arguments = [
        "s7_serving",
        "prepare",
        "--run",
        str(tmp_path / "new"),
        "--run-id",
        "unit",
        "--approval",
        "unit fixture",
    ]
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit) as exc:
        s7_serving.main()
    assert exc.value.code == 2
    assert not (tmp_path / "new").exists()
    with pytest.raises(argparse.ArgumentTypeError, match="approved manifest digest"):
        s7_serving.predecessor_archive("/unapproved/archive")
    captured = []
    monkeypatch.setattr(
        s7_serving, "prepare", lambda *args, **kwargs: captured.append(kwargs) or {}
    )
    monkeypatch.setattr("sys.argv", [*arguments, "--no-predecessors"])
    s7_serving.main()
    assert captured[-1]["expected_predecessor_sends"] == 0
    monkeypatch.setattr(
        "sys.argv",
        [
            *arguments,
            "--expected-predecessor-sends",
            "73",
            "--predecessor-archive",
            str(tmp_path / "archive") + "=sha256:" + "a" * 64,
        ],
    )
    s7_serving.main()
    assert captured[-1]["expected_predecessor_sends"] == 73
    assert captured[-1]["predecessor_archives"] == ((tmp_path / "archive", "sha256:" + "a" * 64),)
