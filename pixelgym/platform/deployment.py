"""Pre-activation verification and atomic deploy/rollback coordination."""

from __future__ import annotations

from collections.abc import Callable

from pixelgym.platform.control_store import (
    CandidateRecord,
    ConflictError,
    ControlStore,
    DeploymentRecord,
    TransitionError,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.mlflow_tracking import Tracking, TrackingMirrorError
from pixelgym.platform.policy import verify_policy_manifest


class DeploymentCoordinator[PreparedCandidate]:
    def __init__(
        self,
        *,
        control: ControlStore,
        store: ImmutableStore,
        load_and_smoke: Callable[[CandidateRecord], PreparedCandidate | bool],
        on_activated: Callable[[DeploymentRecord, PreparedCandidate | bool], None]
        | None = None,
        tracking: Tracking | None = None,
    ) -> None:
        self.control = control
        self.store = store
        self.load_and_smoke = load_and_smoke
        self.on_activated = on_activated or (lambda deployment, prepared: None)
        self.tracking = tracking

    def _mirror_activation(self, deployment: DeploymentRecord) -> None:
        if self.tracking is None:
            return
        try:
            self.tracking.set_champion(deployment.policy_id)
        except TrackingMirrorError as exc:
            self.control.record_tracking_reconciliation(
                subject_id=deployment.deployment_id,
                operation="set_champion_alias",
                error=f"{type(exc).__name__}: {exc}",
                resolved=False,
            )

    def reconcile_tracking(self) -> bool:
        if self.tracking is None:
            return True
        failures = False
        for candidate in self.control.list_candidates():
            gate_status = "eligible" if candidate.gate_report["overall_passed"] else "failed"
            approval_status = (
                "approved"
                if candidate.state.value == "Approved"
                else "pending"
            )
            try:
                self.tracking.mirror_candidate_status(
                    candidate.policy.policy_id,
                    gate_status=gate_status,
                    approval_status=approval_status,
                )
            except TrackingMirrorError as exc:
                failures = True
                self.control.record_tracking_reconciliation(
                    subject_id=candidate.candidate_id,
                    operation="reconcile_candidate_status",
                    error=f"{type(exc).__name__}: {exc}",
                    resolved=False,
                )
        active, _generation = self.control.active()
        if active is not None:
            try:
                self.tracking.set_champion(active.policy_id)
            except TrackingMirrorError as exc:
                failures = True
                self.control.record_tracking_reconciliation(
                    subject_id=active.deployment_id,
                    operation="reconcile_champion_alias",
                    error=f"{type(exc).__name__}: {exc}",
                    resolved=False,
                )
        if not failures:
            self.control.record_tracking_reconciliation(
                subject_id=active.deployment_id if active is not None else "policy-registry",
                operation="reconcile_lifecycle_mirror",
                error=None,
                resolved=True,
            )
        return not failures

    def _verify_candidate(self, candidate_id: str) -> PreparedCandidate | bool:
        candidate, _approval = self.control.verify_candidate_approval(candidate_id)
        verify_policy_manifest(candidate.policy)
        report_sha = sha256_bytes(canonical_json_bytes(candidate.gate_report))
        if report_sha != candidate.gate_report_sha256 or not candidate.gate_report.get("overall_passed"):
            raise TransitionError("approved gate report is corrupt or failed")
        for reference in candidate.artifacts:
            self.store.get_verified(reference)
        prepared = self.load_and_smoke(candidate)
        if not prepared:
            raise TransitionError("candidate failed load or deterministic smoke check")
        return prepared

    def deploy(
        self,
        candidate_id: str,
        *,
        actor: str,
        reason: str,
        expected_deployment_id: str | None = None,
        expected_generation: int | None = None,
    ) -> DeploymentRecord:
        current, generation = self.control.active()
        if expected_generation is not None and (
            generation != expected_generation
            or (current.deployment_id if current else None) != expected_deployment_id
        ):
            raise ConflictError("active deployment changed concurrently")
        prepared = self._verify_candidate(candidate_id)
        result = self.control.activate(
            candidate_id,
            actor=actor,
            reason=reason,
            action="deploy",
            expected_deployment_id=(
                expected_deployment_id
                if expected_generation is not None
                else (current.deployment_id if current else None)
            ),
            expected_generation=(
                expected_generation if expected_generation is not None else generation
            ),
        )
        self.on_activated(result, prepared)
        self._mirror_activation(result)
        return result

    def restore_active(self) -> DeploymentRecord | None:
        """Reverify and load the active deployment during serving startup.

        Any verification or deterministic smoke failure aborts application construction. A
        persisted pointer alone is not sufficient evidence to start the policy ready.
        """
        current, _generation = self.control.active()
        if current is None:
            return None
        prepared = self._verify_candidate(current.candidate_id)
        self.on_activated(current, prepared)
        self._mirror_activation(current)
        return current

    def rollback(
        self,
        *,
        actor: str,
        reason: str,
        expected_deployment_id: str | None = None,
        expected_generation: int | None = None,
    ) -> DeploymentRecord:
        current, generation = self.control.active()
        if current is None:
            raise TransitionError("there is no active deployment")
        if expected_generation is not None and (
            generation != expected_generation or current.deployment_id != expected_deployment_id
        ):
            raise ConflictError("active deployment changed concurrently")
        previous = self.control.previous_target(current)
        prepared = self._verify_candidate(previous.candidate_id)
        result = self.control.activate(
            previous.candidate_id,
            actor=actor,
            reason=reason,
            action="rollback",
            expected_deployment_id=(
                expected_deployment_id if expected_generation is not None else current.deployment_id
            ),
            expected_generation=(
                expected_generation if expected_generation is not None else generation
            ),
        )
        self.on_activated(result, prepared)
        self._mirror_activation(result)
        return result
