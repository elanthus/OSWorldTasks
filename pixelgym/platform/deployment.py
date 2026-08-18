"""Pre-activation verification and atomic deploy/rollback coordination."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pixelgym.platform.control_store import (
    CandidateRecord,
    ConflictError,
    ControlStore,
    DeploymentRecord,
    TransitionError,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import ImmutableStore
from pixelgym.platform.policy import verify_policy_manifest

PreparedCandidate = TypeVar("PreparedCandidate")


class DeploymentCoordinator:
    def __init__(
        self,
        *,
        control: ControlStore,
        store: ImmutableStore,
        load_and_smoke: Callable[[CandidateRecord], PreparedCandidate | bool],
        on_activated: Callable[[DeploymentRecord, PreparedCandidate | bool], None]
        | None = None,
    ) -> None:
        self.control = control
        self.store = store
        self.load_and_smoke = load_and_smoke
        self.on_activated = on_activated or (lambda deployment, prepared: None)

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
        return result
