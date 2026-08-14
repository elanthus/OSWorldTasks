"""Pre-activation verification and atomic deploy/rollback coordination."""

from __future__ import annotations

from collections.abc import Callable

from pixelgym.platform.contracts import CandidateState
from pixelgym.platform.control_store import ControlStore, DeploymentRecord, TransitionError
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.policy import verify_policy_manifest


class DeploymentCoordinator:
    def __init__(
        self,
        *,
        control: ControlStore,
        store: ImmutableStore,
        load_and_smoke: Callable[[object], bool],
        on_activated: Callable[[DeploymentRecord], None] | None = None,
    ) -> None:
        self.control = control
        self.store = store
        self.load_and_smoke = load_and_smoke
        self.on_activated = on_activated or (lambda deployment: None)

    def _verify_candidate(self, candidate_id: str) -> None:
        candidate = self.control.get_candidate(candidate_id)
        if candidate.state is not CandidateState.APPROVED:
            raise TransitionError("candidate must be approved before activation")
        verify_policy_manifest(candidate.policy)
        report_sha = sha256_bytes(canonical_json_bytes(candidate.gate_report))
        if report_sha != candidate.gate_report_sha256 or not candidate.gate_report.get("overall_passed"):
            raise TransitionError("approved gate report is corrupt or failed")
        for reference in candidate.artifacts:
            self.store.get_verified(reference)
        if not self.load_and_smoke(candidate.policy):
            raise TransitionError("candidate failed load or deterministic smoke check")

    def deploy(self, candidate_id: str, *, actor: str, reason: str) -> DeploymentRecord:
        current, generation = self.control.active()
        self._verify_candidate(candidate_id)
        result = self.control.activate(
            candidate_id,
            actor=actor,
            reason=reason,
            action="deploy",
            expected_deployment_id=current.deployment_id if current else None,
            expected_generation=generation,
        )
        self.on_activated(result)
        return result

    def rollback(self, *, actor: str, reason: str) -> DeploymentRecord:
        current, generation = self.control.active()
        if current is None:
            raise TransitionError("there is no active deployment")
        previous = self.control.previous_target(current)
        self._verify_candidate(previous.candidate_id)
        result = self.control.activate(
            previous.candidate_id,
            actor=actor,
            reason=reason,
            action="rollback",
            expected_deployment_id=current.deployment_id,
            expected_generation=generation,
        )
        self.on_activated(result)
        return result
