"""S6 control-plane wiring tests for stateful-v5 packages and the kind-aware runtime.

Every policy here is the no-cost scripted policy running in a local worker subprocess with a
scripted transport. Nothing touches the network, a browser, OSWorld, or a model provider.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import sqlite3
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.runner import ScriptedTransport
from pixelgym.platform.control_store import (
    AuthorizationError,
    CandidateKindMismatchError,
    ConflictError,
    ControlStore,
    DeploymentRecord,
    SyntheticDemoPrincipal,
    TransitionError,
)
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.deployment_smoke import CandidateServiceSmoke, DeploymentSmokeError
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.operational_log import MemoryOperationalLog
from pixelgym.platform.policy_subprocess import (
    LaunchedPolicyWorker,
    PolicyWorkerSpec,
    policy_worker_command,
)
from pixelgym.platform.service import LoadedPolicy, PolicyRuntime, create_serving_app
from pixelgym.platform.stateful_contracts import (
    SESSION_SCHEMA_VERSION,
    EvidenceBinding,
    EvidenceClass,
    StatefulPolicyPackage,
)
from pixelgym.platform.stateful_control import (
    StatefulCandidateRecord,
    StatefulGateObservation,
    StatefulGateReport,
)
from pixelgym.platform.stateful_runtime import (
    ActiveDeploymentEpisodeRegistry,
    KindAwareRuntime,
    PreparedStatefulPolicy,
    StatefulCandidatePreparer,
    StatefulLoadError,
    StatefulRuntime,
    WorkerSpecPolicyLoader,
)
from pixelgym.platform.stateful_service import (
    MemoryEpisodeOperationalLog,
    create_episode_router,
)
from tests.unit.platform.stateful_fixtures import ENDPOINT, GATE_POLICY_SHA256, v5_manifest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}
REVIEWER = "local-reviewer"


class LocalWorkerLauncher:
    """Unenforced local worker; only accepted where a test opts out of the OS sandbox rule."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []

    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker:
        process = subprocess.Popen(
            policy_worker_command(
                runtime_executable=Path(sys.executable),
                worker_path=REPOSITORY_ROOT / "pixelgym/platform/policy_worker.py",
                import_roots=spec.import_roots,
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        )
        self.processes.append(process)
        return LaunchedPolicyWorker(
            process=process,
            mechanism="unit_test_unenforced",
            profile_digest=None,
            os_sandbox_applied=False,
        )


class ExitingLauncher:
    """A worker that dies before it can initialize."""

    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker:
        del spec
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "raise SystemExit(3)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
        )
        return LaunchedPolicyWorker(
            process=process,
            mechanism="unit_test_unenforced",
            profile_digest=None,
            os_sandbox_applied=False,
        )


def _worker_spec(actions: int = 8) -> PolicyWorkerSpec:
    return PolicyWorkerSpec.build(
        factory_module="pixelgym.grounding.v5.runner",
        factory_name="ScriptedStatefulPolicy",
        factory_kwargs={"actions": [NOOP] * actions},
        import_roots=(REPOSITORY_ROOT, Path(sysconfig.get_paths()["purelib"])),
        provider_endpoint=ENDPOINT,
    )


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1024, 768), "white").save(output, format="PNG")
    return output.getvalue()


SCREENSHOT = _png()
SCREENSHOT_SHA256 = "sha256:" + sha256_bytes(SCREENSHOT)


def _screenshot() -> dict[str, str]:
    return {"image_base64": base64.b64encode(SCREENSHOT).decode("ascii"), "media_type": "image/png"}


@dataclasses.dataclass
class Plane:
    control: ControlStore
    store: LocalImmutableStore
    grounding: PolicyRuntime
    stateful: StatefulRuntime
    kinds: KindAwareRuntime
    coordinator: DeploymentCoordinator[Any]
    client: TestClient
    records: MemoryEpisodeOperationalLog
    launcher: LocalWorkerLauncher
    prepared: list[PreparedStatefulPolicy]


def _control(path: Path, *, approved: bool = True) -> ControlStore:
    control = ControlStore(
        path,
        reviewer_identity=REVIEWER,
        approved_stateful_gate_policy_sha256s=(GATE_POLICY_SHA256,) if approved else (),
    )
    control.migrate()
    return control


def _v1_load_and_smoke(candidate: Any) -> LoadedPolicy:
    return LoadedPolicy(
        manifest=candidate.policy,
        deployment_id=f"smoke-{candidate.candidate_id}",
        exact_policy_version=candidate.candidate_id,
        provider=object(),  # type: ignore[arg-type]
    )


def _plane(
    tmp_path: Path,
    *,
    control: ControlStore | None = None,
    preparer_enabled: bool = True,
    preparer_override: Any | None = None,
    launcher: Any | None = None,
    require_os_sandbox: bool = False,
) -> Plane:
    control = control or _control(tmp_path / "control.db")
    store = LocalImmutableStore(tmp_path / "immutable")
    grounding = PolicyRuntime()
    stateful = StatefulRuntime()
    kinds = KindAwareRuntime(grounding, stateful)
    local = LocalWorkerLauncher()
    prepared: list[PreparedStatefulPolicy] = []
    loader = WorkerSpecPolicyLoader(
        worker_specs={"9" * 64: _worker_spec(), "5" * 64: _worker_spec()},
        transport_factory=lambda package: ScriptedTransport(),
        launcher=launcher or local,
        require_os_sandbox=False,
    )
    preparer = StatefulCandidatePreparer(
        loader=loader, state_root=tmp_path / "stateful", require_os_sandbox=require_os_sandbox
    )

    def recording_preparer(candidate: Any, terms: Any) -> PreparedStatefulPolicy:
        result = preparer(candidate, terms)
        prepared.append(result)
        return result

    coordinator: DeploymentCoordinator[Any] = DeploymentCoordinator(
        control=control,
        store=store,
        load_and_smoke=_v1_load_and_smoke,
        on_activated=kinds.activate,
        validate_activation=kinds.check_activatable,
        stateful_load_and_smoke=(
            preparer_override
            if preparer_override is not None
            else recording_preparer
            if preparer_enabled
            else None
        ),
    )
    records = MemoryEpisodeOperationalLog()
    app = create_serving_app(
        grounding,
        operational_log=MemoryOperationalLog(),
        episode_router=create_episode_router(
            host_factory=stateful.host_factory,
            session_registry=ActiveDeploymentEpisodeRegistry(stateful),
            operational_log=records,
        ),
        stateful_readiness=stateful.readiness,
    )
    return Plane(
        control,
        store,
        grounding,
        stateful,
        kinds,
        coordinator,
        TestClient(app),
        records,
        local,
        prepared,
    )


def _stateful_package(
    store: LocalImmutableStore,
    *,
    evidence_class: EvidenceClass = EvidenceClass.CALIBRATION,
    source: str = "9" * 64,
    suffix: str = "a",
) -> tuple[StatefulPolicyPackage, list[Any]]:
    plan = store.put_once(
        f"v5/{suffix}/plan.json",
        b'{"plan":"' + suffix.encode() + b'"}',
        media_type="application/json",
    )
    summary = store.put_once(
        f"v5/{suffix}/summary.json",
        b'{"summary":"' + suffix.encode() + b'"}',
        media_type="application/json",
    )
    evidence = EvidenceBinding(
        run_kind=evidence_class.run_kind,
        run_reference=f"artifacts/v5-run-{suffix}",
        plan_sha256="sha256:" + plan.sha256.removeprefix("sha256:"),
        summary_sha256="sha256:" + summary.sha256.removeprefix("sha256:"),
        assigned_episodes=50,
        attempted_episodes=50,
        exact_successes=35,
    )
    package = StatefulPolicyPackage.build(
        manifest=v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256=source,
        dependency_lock_sha256="8" * 64,
        max_steps=40,
        evidence_class=evidence_class,
        evidence=evidence,
        code_revision="2" * 40,
        code_state="clean",
        source_tree_sha256="7" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )
    return package, [plan, summary]


def _report(package: StatefulPolicyPackage, *, passed: bool = True) -> StatefulGateReport:
    return StatefulGateReport(
        gate_policy_version="test-only-stateful-gate-policy",
        gate_policy_sha256=GATE_POLICY_SHA256,
        policy_id=package.policy_id,
        run_reference=package.evidence.run_reference,
        evidence_class=package.evidence_class,
        evidence_plan_sha256=package.evidence.plan_sha256,
        evidence_summary_sha256=package.evidence.summary_sha256,
        observations=(
            StatefulGateObservation(
                name="exact_episode_success",
                observed=0.7 if passed else 0.2,
                threshold=0.5,
                comparator="at_least",
                passed=passed,
            ),
        ),
        code_revision_passed=True,
        overall_passed=passed,
        reasons=() if passed else ("exact_episode_success below threshold",),
    )


def _stateful_candidate(
    plane: Plane,
    *,
    cap: int = 50,
    tier: str = "demo",
    terms: bool = True,
    **package_options: Any,
) -> StatefulCandidateRecord:
    package, artifacts = _stateful_package(plane.store, **package_options)
    candidate = plane.control.register_stateful_candidate(
        package=package, gate_report=_report(package), artifacts=artifacts
    )
    plane.control.approve(
        candidate.candidate_id,
        actor=REVIEWER,
        reason="reviewed stateful package",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    if terms:
        plane.control.approve_stateful_serving_terms(
            candidate.candidate_id,
            actor=REVIEWER,
            reason="demo cap reviewed",
            deployment_attempt_cap=cap,
            deployment_tier=tier,
        )
    return candidate


def _grounding_candidate(plane: Plane, passing_evidence: Any, suffix: str = "") -> Any:
    policy, summary, report = passing_evidence
    if suffix:
        from pixelgym.platform.policy import build_policy_manifest, prompt_template
        from pixelgym.platform.source_provenance import SourceProvenance

        original = passing_evidence[0]
        policy = build_policy_manifest(
            provider=original.provider,
            model=original.model + "-" + suffix,
            prompt_name=original.prompt_name,
            prompt_version=original.prompt_version,
            prompt=prompt_template(original.prompt_version),
            condition=original.condition,
            parameters=original.parameters,
            parser_version=original.parser_version,
            scorer_version=original.scorer_version,
            overlay_version=original.overlay_version,
            target_semantics=original.target_semantics,
            source_provenance=SourceProvenance(
                "pixelgym-source-provenance-v1",
                original.code_revision,
                original.source_tree_sha256,
                original.code_state,
                "git-build-inputs-v1",
            ),
            dependency_lock_sha256=original.dependency_lock_sha256,
        )
    reference = plane.store.put_once(
        f"policy/{suffix or 'a'}.json", b"verified", media_type="application/json"
    )
    candidate = plane.control.register_candidate(
        source_run_id=summary.run_id + suffix,
        policy=policy,
        gate_report=dataclasses.replace(
            report, policy_id=policy.policy_id, run_id=summary.run_id + suffix
        ),
        artifacts=[reference],
    )
    plane.control.approve(
        candidate.candidate_id,
        actor=REVIEWER,
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    return candidate


def _create(plane: Plane, ref: str = "client-1") -> Any:
    return plane.client.post(
        "/api/v2/episodes",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "task_instruction": "Complete the vendor form.",
            "screen_width": 1024,
            "screen_height": 768,
            "client_episode_ref": ref,
        },
    )


def _act(plane: Plane, episode_id: str, previous: str | None = None) -> Any:
    return plane.client.post(
        f"/api/v2/episodes/{episode_id}/act",
        json={
            "schema_version": SESSION_SCHEMA_VERSION,
            "screenshot": _screenshot(),
            "previous_intent_id": previous,
            "previous_result": None
            if previous is None
            else {
                "reward": 0.0,
                "terminated": False,
                "truncated": False,
                "screenshot_sha256": SCREENSHOT_SHA256,
            },
        },
    )


def _deployment_record(candidate_id: str, policy_id: str) -> DeploymentRecord:
    return DeploymentRecord(
        deployment_id="deployment-test",
        candidate_id=candidate_id,
        policy_id=policy_id,
        action="deploy",
        actor=REVIEWER,
        reason="test",
        created_at_utc="2026-10-05T00:00:00Z",
        generation=1,
    )


# Registration and approval -------------------------------------------------------------------


def test_registration_requires_an_approved_v5_gate_policy(tmp_path: Path) -> None:
    plane = _plane(tmp_path, control=_control(tmp_path / "control.db", approved=False))
    package, artifacts = _stateful_package(plane.store)

    with pytest.raises(TransitionError, match="approved v5 gate policy"):
        plane.control.register_stateful_candidate(
            package=package, gate_report=_report(package), artifacts=artifacts
        )

    assert plane.control.list_stateful_candidates() == []


def test_registration_rejects_unbound_evidence_and_mismatched_reports(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    package, artifacts = _stateful_package(plane.store)
    other, _ = _stateful_package(plane.store, suffix="b")

    with pytest.raises(ValueError, match="evidence plan and summary"):
        plane.control.register_stateful_candidate(
            package=package, gate_report=_report(package), artifacts=artifacts[:1]
        )
    with pytest.raises(ValueError, match="policy identity"):
        plane.control.register_stateful_candidate(
            package=package, gate_report=_report(other), artifacts=artifacts
        )
    with pytest.raises(ValueError, match="contradicts"):
        StatefulGateObservation(
            name="exact_episode_success",
            observed=0.4,
            threshold=0.5,
            comparator="at_least",
            passed=True,
        )
    with pytest.raises(ValueError, match="gated observations"):
        dataclasses.replace(_report(package), observations=())


def test_failed_stateful_gate_report_cannot_be_approved(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    package, artifacts = _stateful_package(plane.store)
    candidate = plane.control.register_stateful_candidate(
        package=package, gate_report=_report(package, passed=False), artifacts=artifacts
    )

    assert candidate.state.value == "GateFailed"
    with pytest.raises(TransitionError, match="eligible"):
        plane.control.approve(
            candidate.candidate_id,
            actor=REVIEWER,
            reason="no",
            gate_report_sha256=candidate.gate_report_sha256,
        )


def test_grounding_views_are_unchanged_by_stateful_rows(tmp_path: Path, passing_evidence) -> None:
    plane = _plane(tmp_path)
    grounding = _grounding_candidate(plane, passing_evidence)
    stateful = _stateful_candidate(plane)

    assert [item.candidate_id for item in plane.control.list_candidates()] == [
        grounding.candidate_id
    ]
    assert plane.control.list_candidate_providers() == [grounding.policy.provider]
    assert [item.candidate_id for item in plane.control.list_stateful_candidates()] == [
        stateful.candidate_id
    ]
    with pytest.raises(KeyError):
        plane.control.get_candidate(stateful.candidate_id)
    with pytest.raises(KeyError):
        plane.control.get_stateful_candidate(grounding.candidate_id)
    assert plane.control.candidate_kind(stateful.candidate_id).value == "stateful-v5"
    assert plane.control.candidate_kind(grounding.candidate_id).value == "grounding"


def test_lifecycle_paths_refuse_the_other_kind(tmp_path: Path, passing_evidence) -> None:
    plane = _plane(tmp_path)
    grounding = _grounding_candidate(plane, passing_evidence)
    stateful = _stateful_candidate(plane)

    with pytest.raises(CandidateKindMismatchError):
        plane.control.verify_candidate_approval(stateful.candidate_id)
    with pytest.raises(CandidateKindMismatchError):
        plane.control.verify_stateful_candidate_approval(grounding.candidate_id)
    with pytest.raises(CandidateKindMismatchError):
        plane.control.approve_stateful_serving_terms(
            grounding.candidate_id,
            actor=REVIEWER,
            reason="no",
            deployment_attempt_cap=1,
            deployment_tier="demo",
        )
    with pytest.raises(CandidateKindMismatchError):
        CandidateServiceSmoke(fixture=None, provider=None)(stateful)  # type: ignore[arg-type]
    with pytest.raises(CandidateKindMismatchError):
        StatefulCandidatePreparer(loader=None, state_root=tmp_path)(  # type: ignore[arg-type]
            grounding, plane.control.get_stateful_serving_terms(stateful.candidate_id)
        )


def test_serving_terms_bind_tier_to_evidence_class(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    calibration = _stateful_candidate(plane, terms=False)
    confirmatory = _stateful_candidate(
        plane, terms=False, evidence_class=EvidenceClass.CONFIRMATORY, suffix="c"
    )

    with pytest.raises(TransitionError, match="only a demo deployment"):
        plane.control.approve_stateful_serving_terms(
            calibration.candidate_id,
            actor=REVIEWER,
            reason="too far",
            deployment_attempt_cap=10,
            deployment_tier="standard",
        )
    with pytest.raises(AuthorizationError):
        plane.control.approve_stateful_serving_terms(
            calibration.candidate_id,
            actor="someone-else",
            reason="unverified",
            deployment_attempt_cap=10,
            deployment_tier="demo",
        )
    with pytest.raises(ValueError, match="positive"):
        plane.control.approve_stateful_serving_terms(
            calibration.candidate_id,
            actor=REVIEWER,
            reason="zero",
            deployment_attempt_cap=0,
            deployment_tier="demo",
        )
    terms = plane.control.approve_stateful_serving_terms(
        confirmatory.candidate_id,
        actor=REVIEWER,
        reason="confirmatory",
        deployment_attempt_cap=10,
        deployment_tier="standard",
    )
    assert terms.deployment_tier.value == "standard"
    with pytest.raises(ConflictError):
        plane.control.approve_stateful_serving_terms(
            confirmatory.candidate_id,
            actor=REVIEWER,
            reason="confirmatory",
            deployment_attempt_cap=11,
            deployment_tier="standard",
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        plane.control.connection.execute(
            "UPDATE stateful_serving_terms SET deployment_attempt_cap = 99"
        )


# Deployment, activation, and rollback --------------------------------------------------------


def test_stateful_deploy_without_terms_or_preparer_leaves_traffic_unchanged(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path)
    grounding = _grounding_candidate(plane, passing_evidence)
    plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")
    no_terms = _stateful_candidate(plane, terms=False)

    with pytest.raises(TransitionError, match="serving terms"):
        plane.coordinator.deploy(no_terms.candidate_id, actor=REVIEWER, reason="no terms")

    (tmp_path / "other").mkdir()
    unconfigured = _plane(tmp_path / "other", preparer_enabled=False)
    candidate = _stateful_candidate(unconfigured)
    with pytest.raises(CandidateKindMismatchError, match="not configured"):
        unconfigured.coordinator.deploy(candidate.candidate_id, actor=REVIEWER, reason="refused")

    assert plane.control.active()[0].candidate_id == grounding.candidate_id
    assert plane.grounding.loaded is not None
    assert unconfigured.control.active()[0] is None
    assert unconfigured.launcher.processes == []


def test_runtimes_refuse_prepared_policies_of_the_other_kind(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path)
    grounding = _grounding_candidate(plane, passing_evidence)
    stateful = _stateful_candidate(plane)
    terms = plane.control.get_stateful_serving_terms(stateful.candidate_id)
    prepared = plane.coordinator.stateful_load_and_smoke(stateful, terms)  # type: ignore[misc]
    loaded = _v1_load_and_smoke(grounding)
    try:
        with pytest.raises(TypeError, match="grounding"):
            PolicyRuntime().activate(prepared)  # type: ignore[arg-type]
        with pytest.raises(CandidateKindMismatchError):
            StatefulRuntime().activate(
                _deployment_record(grounding.candidate_id, grounding.policy.policy_id), loaded
            )
        with pytest.raises(TransitionError, match="does not match"):
            StatefulRuntime().activate(
                _deployment_record(grounding.candidate_id, grounding.policy.policy_id), prepared
            )
        with pytest.raises(TypeError, match="grounding"):
            PolicyRuntime.check_activatable(prepared)  # type: ignore[arg-type]
        with pytest.raises(CandidateKindMismatchError):
            StatefulRuntime.check_activatable(loaded)
        with pytest.raises(CandidateKindMismatchError):
            plane.kinds.check_activatable(object())
        plane.kinds.check_activatable(prepared)
        plane.kinds.check_activatable(loaded)
        with pytest.raises(CandidateKindMismatchError):
            plane.kinds.activate(
                _deployment_record(stateful.candidate_id, stateful.policy_id), object()
            )
    finally:
        prepared.close()
    assert plane.kinds.active_kind is None


def test_v5_serves_episodes_and_rollback_crosses_kinds(tmp_path: Path, passing_evidence) -> None:
    plane = _plane(tmp_path)
    grounding = _grounding_candidate(plane, passing_evidence)
    stateful = _stateful_candidate(plane, cap=50)
    first = plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")
    assert plane.client.get("/health/ready").json()["policy_id"] == grounding.policy.policy_id
    assert _create(plane).json()["detail"]["code"] == "no_active_deployment"

    deployed = plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")

    assert plane.kinds.active_kind.value == "stateful-v5"
    assert plane.grounding.loaded is None
    assert plane.client.get("/api/v1/policy").status_code == 503
    assert plane.client.get("/health/ready").json() == {
        "status": "ready",
        "policy_id": stateful.policy_id,
        "kind": "stateful-v5",
    }
    created = _create(plane)
    assert created.status_code == 201
    identity = created.json()["identity"]
    assert identity == {
        "policy_id": stateful.policy_id,
        "deployment_id": deployed.deployment_id,
        "exact_policy_version": stateful.candidate_id,
        "evidence_class": "calibration",
    }
    assert created.headers["X-PixelGym-Evidence-Class"] == "calibration"
    episode_id = created.json()["episode_id"]
    acted = _act(plane, episode_id)
    assert acted.status_code == 200
    assert acted.json()["action"] == {"action_type": "NOOP", "x": None, "y": None, "key": None}
    second = _act(plane, episode_id, acted.json()["intent_id"])
    assert second.status_code == 200 and second.json()["step_index"] == 1
    event = next(
        item
        for item in plane.control.audit_events()
        if item["subject_id"] == deployed.deployment_id
    )
    assert event["details"]["kind"] == "stateful-v5"
    assert event["details"]["deployment_attempt_cap"] == 50
    assert event["details"]["deployment_tier"] == "demo"

    rolled = plane.coordinator.rollback(actor=REVIEWER, reason="back to v1")

    assert rolled.candidate_id == grounding.candidate_id
    assert rolled.deployment_id != first.deployment_id
    assert plane.kinds.active_kind.value == "grounding"
    assert plane.grounding.loaded.deployment_id == rolled.deployment_id
    stale = _act(plane, episode_id, second.json()["intent_id"])
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "deployment_changed"
    assert _create(plane, "client-2").json()["detail"]["code"] == "no_active_deployment"
    assert plane.prepared[0].loaded.policy._launched.process.poll() is not None


def test_rollback_from_grounding_restores_a_stateful_version(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)
    grounding = _grounding_candidate(plane, passing_evidence)
    first = plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")
    old_episode = _create(plane).json()["episode_id"]
    plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")

    restored = plane.coordinator.rollback(actor=REVIEWER, reason="back to v5")

    assert restored.candidate_id == stateful.candidate_id
    assert restored.deployment_id != first.deployment_id
    assert plane.kinds.active_kind.value == "stateful-v5"
    assert plane.stateful.active.identity.deployment_id == restored.deployment_id
    created = _create(plane, "client-after-rollback")
    assert created.status_code == 201
    assert created.json()["identity"]["deployment_id"] == restored.deployment_id
    # An episode opened under the earlier deployment of the same version is not resumed.
    assert _act(plane, old_episode).json()["detail"]["code"] == "deployment_changed"


def test_deployment_attempt_cap_survives_redeploy_and_rollback(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane, cap=1)
    grounding = _grounding_candidate(plane, passing_evidence)
    plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")
    episode_id = _create(plane).json()["episode_id"]
    assert _act(plane, episode_id).json()["sealed_failure"] is None

    refused = _create(plane, "client-2")
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "deployment_attempt_cap_reached"

    current = plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")
    with pytest.raises(StatefulLoadError, match="already exhausted"):
        plane.coordinator.rollback(actor=REVIEWER, reason="cap must persist")
    assert plane.control.active()[0] == current
    assert plane.kinds.active_kind.value == "grounding"


def test_worker_start_failure_blocks_deploy_and_keeps_traffic(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path, launcher=ExitingLauncher())
    grounding = _grounding_candidate(plane, passing_evidence)
    stateful = _stateful_candidate(plane)
    active = plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")

    with pytest.raises(StatefulLoadError, match="isolated process"):
        plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="must fail")

    assert isinstance(StatefulLoadError("x"), DeploymentSmokeError)
    assert plane.control.active()[0] == active
    assert plane.grounding.loaded is not None


def test_unsandboxed_worker_is_refused_by_default(tmp_path: Path) -> None:
    plane = _plane(tmp_path, require_os_sandbox=True)
    stateful = _stateful_candidate(plane)

    with pytest.raises(StatefulLoadError, match="OS sandbox"):
        plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="must fail")

    assert plane.control.active()[0] is None
    assert all(process.poll() is not None for process in plane.launcher.processes)


def test_unregistered_package_source_is_refused_before_any_worker_starts(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane, source="4" * 64)

    with pytest.raises(StatefulLoadError, match="no policy worker"):
        plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="must fail")

    assert plane.launcher.processes == []
    assert plane.control.active()[0] is None


def test_worker_death_after_activation_seals_infrastructure_failure(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)
    plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")
    episode_id = _create(plane).json()["episode_id"]
    process = plane.prepared[-1].loaded.policy._launched.process
    process.kill()
    process.wait(timeout=5)

    acted = _act(plane, episode_id)

    assert acted.status_code == 200
    assert acted.json()["sealed_failure"] == "infrastructure_failure"
    assert acted.json()["action"] is None


def test_lost_activation_race_releases_the_prepared_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)

    def lose(*args: Any, **kwargs: Any) -> Any:
        raise ConflictError("atomic activation compare-and-swap lost")

    monkeypatch.setattr(plane.control, "activate", lose)
    with pytest.raises(ConflictError):
        plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="race")

    assert plane.prepared[0]._closed
    assert plane.prepared[0].loaded.policy._launched.process.poll() is not None
    assert plane.kinds.active_kind is None
    failure = plane.control.audit_events()[-1]
    assert failure["event_type"] == "deployment.deploy_failed"
    assert failure["details"]["stage"] == "transaction"
    assert failure["details"]["candidate_id"] == stateful.candidate_id


def test_stateful_kind_mismatch_is_rejected_before_the_cas(
    tmp_path: Path, passing_evidence
) -> None:
    holder: dict[str, Plane] = {}

    def wrong_kind(candidate: Any, terms: Any) -> LoadedPolicy:
        # A misconfigured preparer hands back a grounding runtime for a stateful candidate.
        return _v1_load_and_smoke(holder["grounding"])

    plane = _plane(tmp_path, preparer_override=wrong_kind)
    grounding = _grounding_candidate(plane, passing_evidence)
    holder["grounding"] = grounding
    stateful = _stateful_candidate(plane)
    active = plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")
    pointer_before = plane.control.active()

    with pytest.raises(CandidateKindMismatchError, match="kind or identity"):
        plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="wrong kind")

    assert plane.control.active() == pointer_before
    assert plane.control.active()[0] == active
    assert plane.kinds.active_kind.value == "grounding"
    assert plane.grounding.loaded.deployment_id == active.deployment_id
    failure = plane.control.audit_events()[-1]
    assert failure["event_type"] == "deployment.deploy_failed"
    assert failure["details"]["stage"] == "preactivation"
    assert failure["details"]["candidate_id"] == stateful.candidate_id
    assert failure["details"]["error_class"] == "CandidateKindMismatchError"
    assert failure["details"]["authoritative_commit"] is False


def test_stateful_rollback_failure_records_a_preactivation_event(
    tmp_path: Path, passing_evidence
) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane, cap=1)
    grounding = _grounding_candidate(plane, passing_evidence)
    plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")
    episode_id = _create(plane).json()["episode_id"]
    assert _act(plane, episode_id).json()["sealed_failure"] is None
    current = plane.coordinator.deploy(grounding.candidate_id, actor=REVIEWER, reason="v1")

    with pytest.raises(StatefulLoadError):
        plane.coordinator.rollback(actor=REVIEWER, reason="cap exhausted")

    assert plane.control.active()[0] == current
    failure = plane.control.audit_events()[-1]
    assert failure["event_type"] == "deployment.rollback_failed"
    assert failure["details"]["stage"] == "preactivation"
    assert failure["details"]["candidate_id"] == stateful.candidate_id


def test_withdrawn_gate_policy_blocks_activation(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)
    reopened = _plane(
        tmp_path / "reopened", control=_control(tmp_path / "control.db", approved=False)
    )

    with pytest.raises(TransitionError, match="approved v5 gate policy"):
        reopened.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="refused")

    assert reopened.control.active()[0] is None
    assert reopened.launcher.processes == []


def test_restart_restores_the_stateful_deployment_and_its_open_episodes(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)
    deployed = plane.coordinator.deploy(stateful.candidate_id, actor=REVIEWER, reason="v5")
    episode_id = _create(plane).json()["episode_id"]
    first = _act(plane, episode_id).json()
    plane.stateful.deactivate()

    restarted = _plane(tmp_path, control=plane.control)
    restored = restarted.coordinator.restore_active()

    assert restored == deployed
    assert restarted.stateful.active.identity.deployment_id == deployed.deployment_id
    resumed = _act(restarted, episode_id, first["intent_id"])
    assert resumed.status_code == 200
    assert resumed.json()["step_index"] == 1
    assert resumed.json()["identity"]["deployment_id"] == deployed.deployment_id


def test_preparer_smoke_runs_isolated_from_deployment_stores(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    stateful = _stateful_candidate(plane)
    terms = plane.control.get_stateful_serving_terms(stateful.candidate_id)

    prepared = plane.coordinator.stateful_load_and_smoke(stateful, terms)  # type: ignore[misc]
    try:
        assert prepared.journal.call_counts()[0] == 0
        assert prepared.session_store.records("ep-" + "0" * 32) == ()
        assert prepared.loaded.policy._launched.process.poll() is None
    finally:
        prepared.close()
    assert prepared.loaded.policy._launched.process.poll() is not None
    assert plane.control.active()[0] is None


# Bootstrap wiring ----------------------------------------------------------------------------


def _bootstrap_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ControlStore:
    monkeypatch.setenv("PIXELGYM_REPOSITORY_ROOT", str(REPOSITORY_ROOT))
    monkeypatch.setenv("PIXELGYM_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("PIXELGYM_IMMUTABLE_ROOT", str(tmp_path / "immutable"))
    monkeypatch.setenv("PIXELGYM_CSRF_SECRET", "test-secret-at-least-sixteen")
    for name in ("PIXELGYM_IMMUTABLE_BUCKET", "MLFLOW_TRACKING_URI"):
        monkeypatch.delenv(name, raising=False)
    control = ControlStore(
        tmp_path / "control.db",
        reviewer_identity=REVIEWER,
        approved_stateful_gate_policy_sha256s=(GATE_POLICY_SHA256,),
    )
    control.migrate()
    return control


def _create_body() -> dict[str, Any]:
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "task_instruction": "Complete the vendor form.",
        "screen_width": 1024,
        "screen_height": 768,
        "client_episode_ref": "client-1",
    }


def _register_approved_with_terms(control: ControlStore, tmp_path: Path) -> Any:
    store = LocalImmutableStore(tmp_path / "immutable")
    package, artifacts = _stateful_package(store)
    candidate = control.register_stateful_candidate(
        package=package, gate_report=_report(package), artifacts=artifacts
    )
    control.approve(
        candidate.candidate_id,
        actor=REVIEWER,
        reason="reviewed",
        gate_report_sha256=candidate.gate_report_sha256,
    )
    control.approve_stateful_serving_terms(
        candidate.candidate_id,
        actor=REVIEWER,
        reason="demo",
        deployment_attempt_cap=5,
        deployment_tier="demo",
    )
    return candidate


def test_bootstrap_mounts_api_v2_closed_until_stateful_serving_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pixelgym.platform import bootstrap

    control = _bootstrap_env(tmp_path, monkeypatch)
    app = bootstrap.create_app()
    client = TestClient(app)

    assert client.get("/health/ready").status_code == 503
    response = client.post("/api/v2/episodes", json=_create_body())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_active_deployment"
    # Without configured gate-policy digests the app's own ledger refuses stateful candidates.
    store = LocalImmutableStore(tmp_path / "immutable")
    package, artifacts = _stateful_package(store, suffix="z")
    with pytest.raises(TransitionError, match="approved v5 gate policy"):
        app.state.deployment_coordinator.control.register_stateful_candidate(
            package=package, gate_report=_report(package), artifacts=artifacts
        )
    candidate = _register_approved_with_terms(control, tmp_path)
    with pytest.raises(TransitionError, match="approved v5 gate policy"):
        app.state.deployment_coordinator.deploy(
            candidate.candidate_id, actor=SyntheticDemoPrincipal(), reason="not configured"
        )
    assert control.active()[0] is None


def test_bootstrap_serves_a_stateful_deployment_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pixelgym.platform import bootstrap

    control = _bootstrap_env(tmp_path, monkeypatch)
    loader = WorkerSpecPolicyLoader(
        worker_specs={"9" * 64: _worker_spec()},
        transport_factory=lambda package: ScriptedTransport(),
        launcher=LocalWorkerLauncher(),
        require_os_sandbox=False,
    )
    app = bootstrap.create_app(
        stateful_serving=bootstrap.StatefulServingConfig(
            approved_gate_policy_sha256s=frozenset({GATE_POLICY_SHA256}),
            preparer=StatefulCandidatePreparer(
                loader=loader, state_root=tmp_path / "stateful", require_os_sandbox=False
            ),
        )
    )
    client = TestClient(app)
    candidate = _register_approved_with_terms(control, tmp_path)

    deployed = app.state.deployment_coordinator.deploy(
        candidate.candidate_id, actor=SyntheticDemoPrincipal(), reason="configured"
    )

    assert client.get("/health/ready").json()["kind"] == "stateful-v5"
    created = client.post("/api/v2/episodes", json=_create_body())
    assert created.status_code == 201
    assert created.headers["X-PixelGym-Deployment-ID"] == deployed.deployment_id
    episode_id = created.json()["episode_id"]
    assert (tmp_path / f"immutable/objects/serving-episode-records/{episode_id}").is_dir()
    app.state.stateful_runtime.deactivate()
