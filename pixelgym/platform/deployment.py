"""Pre-activation verification and atomic deploy/rollback coordination.

Failure-safe strategy for the post-CAS handoff (issue #166, D4.10): the runtime activation
callback is *total by construction*. Every check that can reject a prepared candidate runs
before the authoritative compare-and-swap, in ``load_and_smoke`` and ``validate_activation``;
``on_activated`` may only install the already-validated value. A durable compensating
transition was not chosen because it would be an automatic rollback, which the platform design
places out of scope, and because compensation needs its own post-CAS runtime activation that
can fail the same way.

As a backstop, a failure that escapes ``on_activated`` anyway is recorded as a
``runtime_activation`` failure event naming the committed deployment and is re-raised. The
ledger is never rewritten; serving-startup restore converges the runtime to the ledger.

Every failed deploy or rollback by an authorized reviewer appends one
``deployment.deploy_failed`` or ``deployment.rollback_failed`` audit event whose ``stage`` is
``preactivation``, ``transaction``, or ``runtime_activation``.

The coordinator is kind-aware (v5 serving stage S6). Grounding candidates follow the
Milestone 4 path unchanged. A ``stateful-v5`` candidate is verified through its own stored
contracts, approval, and serving terms, then prepared by a separately injected
``stateful_load_and_smoke``. Before the compare-and-swap the coordinator also proves that the
prepared value's kind and identity match the verified candidate, and ``validate_activation``
runs for both kinds, so the post-CAS handoff stays total. Both kinds activate through the same
pointer, so deploy and rollback may cross kinds; failures of either kind record the same
failure events. A coordinator without a stateful preparer refuses stateful candidates before
any load. A prepared candidate that never reaches traffic is released.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from pixelgym.platform.control_store import (
    CandidateKindMismatchError,
    CandidateRecord,
    ConflictError,
    ContentionError,
    ControlStore,
    DeploymentRecord,
    TransitionError,
)
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes
from pixelgym.platform.immutable_store import ImmutableStore, ImmutableStoreError
from pixelgym.platform.mlflow_tracking import Tracking, TrackingMirrorError
from pixelgym.platform.policy import verify_policy_manifest, verify_renderer_binding
from pixelgym.platform.schema_validation import ContractValidationError
from pixelgym.platform.stateful_control import (
    PolicyKind,
    ServingTerms,
    StatefulCandidateRecord,
)

# Control-plane exceptions whose messages are fixed, credential-free strings. Any other
# exception is recorded by class name only, so provider or library detail never enters the
# append-only audit ledger.
_DESCRIBED_FAILURES: tuple[type[BaseException], ...] = (
    TransitionError,
    ConflictError,
    ContentionError,
    ContractValidationError,
    ImmutableStoreError,
)


def _discard_prepared(prepared: object) -> None:
    """Release resources a prepared candidate holds when it never reaches traffic."""
    close = getattr(prepared, "close", None)
    if callable(close):
        close()


@dataclass(frozen=True)
class _Attempt:
    action: str
    actor: str
    reason: str
    subject_id: str
    candidate_id: str | None
    expected_deployment_id: str | None
    expected_generation: int | None


class DeploymentCoordinator[PreparedCandidate]:
    def __init__(
        self,
        *,
        control: ControlStore,
        store: ImmutableStore,
        load_and_smoke: Callable[[CandidateRecord], PreparedCandidate | bool],
        on_activated: Callable[[DeploymentRecord, PreparedCandidate | bool], None] | None = None,
        tracking: Tracking | None = None,
        validate_activation: Callable[[PreparedCandidate | bool], None] | None = None,
        stateful_load_and_smoke: (
            Callable[[StatefulCandidateRecord, ServingTerms], PreparedCandidate] | None
        ) = None,
    ) -> None:
        """Coordinate verified activation.

        ``validate_activation`` runs before the compare-and-swap and must raise for any
        prepared value that ``on_activated`` could not install. ``on_activated`` runs after the
        compare-and-swap commits and must be total: it may only install the validated value.
        """
        self.control = control
        self.store = store
        self.load_and_smoke = load_and_smoke
        self.on_activated = on_activated or (lambda deployment, prepared: None)
        self.validate_activation = validate_activation or (lambda prepared: None)
        self.stateful_load_and_smoke = stateful_load_and_smoke
        self.tracking = tracking

    def _record_failure(
        self,
        attempt: _Attempt,
        stage: str,
        error: BaseException,
        *,
        committed_deployment_id: str | None = None,
    ) -> None:
        """Append the failure event; never mask ``error`` if recording itself fails."""
        try:
            self.control.record_deployment_failure(
                action=attempt.action,
                stage=stage,
                actor=attempt.actor,
                reason=attempt.reason,
                subject_id=attempt.subject_id,
                candidate_id=attempt.candidate_id,
                error_class=type(error).__name__,
                error_message=(str(error) if isinstance(error, _DESCRIBED_FAILURES) else None),
                expected_deployment_id=attempt.expected_deployment_id,
                expected_generation=attempt.expected_generation,
                committed_deployment_id=committed_deployment_id,
            )
        except Exception as record_error:  # noqa: BLE001 - the original failure must surface.
            error.add_note(
                f"deployment failure event was not recorded: {type(record_error).__name__}"
            )

    def _activate_runtime(
        self, attempt: _Attempt, deployment: DeploymentRecord, prepared: PreparedCandidate | bool
    ) -> None:
        try:
            self.on_activated(deployment, prepared)
        except BaseException as exc:
            # The ledger already committed; record the divergence instead of hiding it.
            self._record_failure(
                attempt,
                "runtime_activation",
                exc,
                committed_deployment_id=deployment.deployment_id,
            )
            raise

    def _mirror_activation(self, deployment: DeploymentRecord) -> None:
        if self.tracking is None:
            return
        if self.control.candidate_kind(deployment.candidate_id) is PolicyKind.STATEFUL_V5:
            # Stateful packages are not registered in the MLflow grounding registry, so there is
            # no champion version to mirror; the control plane remains the ledger.
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
            gate_status = "eligible" if candidate.gate_report.overall_passed else "failed"
            approval_status = "approved" if candidate.state.value == "Approved" else "pending"
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
        if (
            active is not None
            and self.control.candidate_kind(active.candidate_id) is PolicyKind.GROUNDING
        ):
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
        if self.control.candidate_kind(candidate_id) is PolicyKind.STATEFUL_V5:
            return self._verify_stateful_candidate(candidate_id)
        candidate, _approval = self.control.verify_candidate_approval(candidate_id)
        verify_policy_manifest(candidate.policy)
        # Blocks activation -- deploy, rollback, and the serving-startup restore below --
        # for any candidate whose packaged renderer is missing, unsupported, or does not
        # match the renderer code actually running. Older evidence lacking renderer
        # identity stays readable elsewhere; it simply cannot reach traffic again.
        verify_renderer_binding(candidate.policy)
        report_sha = sha256_bytes(canonical_json_bytes(candidate.gate_report.to_dict()))
        if report_sha != candidate.gate_report_sha256 or not candidate.gate_report.overall_passed:
            raise TransitionError("approved gate report is corrupt or failed")
        for reference in candidate.artifacts:
            self.store.get_verified(reference)
        prepared = self.load_and_smoke(candidate)
        if not prepared:
            raise TransitionError("candidate failed load or deterministic smoke check")
        try:
            if getattr(prepared, "kind", None) is PolicyKind.STATEFUL_V5:
                raise CandidateKindMismatchError(
                    "a grounding candidate was prepared as a stateful-v5 policy"
                )
            self.validate_activation(prepared)
        except BaseException:
            _discard_prepared(prepared)
            raise
        return prepared

    def _verify_stateful_candidate(self, candidate_id: str) -> PreparedCandidate:
        """Reverify a stateful candidate's stored evidence and serving terms, then prepare it.

        Verification order matches the grounding path: approval and contract evidence, the
        approved gate-report digest, every immutable artifact, and only then the load and
        no-cost smoke. The prepared value's kind and identity are then checked against the
        verified candidate, followed by ``validate_activation``. Nothing here changes traffic.
        """
        candidate, _approval, terms = self.control.verify_stateful_candidate_approval(candidate_id)
        report_sha = sha256_bytes(canonical_json_bytes(candidate.gate_report.to_dict()))
        if report_sha != candidate.gate_report_sha256 or not candidate.gate_report.overall_passed:
            raise TransitionError("approved gate report is corrupt or failed")
        for reference in candidate.artifacts:
            self.store.get_verified(reference)
        if self.stateful_load_and_smoke is None:
            raise CandidateKindMismatchError(
                "this control plane is not configured to serve stateful-v5 packages"
            )
        prepared = self.stateful_load_and_smoke(candidate, terms)
        try:
            if not prepared:
                raise TransitionError("candidate failed load or deterministic smoke check")
            if (
                getattr(prepared, "kind", None) is not PolicyKind.STATEFUL_V5
                or getattr(prepared, "candidate_id", None) != candidate.candidate_id
                or getattr(prepared, "policy_id", None) != candidate.policy_id
            ):
                raise CandidateKindMismatchError(
                    "prepared policy kind or identity does not match the stateful candidate"
                )
            self.validate_activation(prepared)
        except BaseException:
            _discard_prepared(prepared)
            raise
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
        # An unverified actor is rejected before any work and leaves no ledger row.
        self.control.require_reviewer_actor(actor)
        attempt = _Attempt(
            action="deploy",
            actor=actor,
            reason=reason,
            subject_id=candidate_id,
            candidate_id=candidate_id,
            expected_deployment_id=expected_deployment_id,
            expected_generation=expected_generation,
        )
        try:
            current, generation = self.control.active()
            if expected_generation is not None and (
                generation != expected_generation
                or (current.deployment_id if current else None) != expected_deployment_id
            ):
                raise ConflictError("active deployment changed concurrently")
            prepared = self._verify_candidate(candidate_id)
        except Exception as exc:
            self._record_failure(attempt, "preactivation", exc)
            raise
        if expected_generation is None:
            attempt = replace(
                attempt,
                expected_deployment_id=current.deployment_id if current else None,
                expected_generation=generation,
            )
        try:
            result = self.control.activate(
                candidate_id,
                actor=actor,
                reason=reason,
                action="deploy",
                expected_deployment_id=attempt.expected_deployment_id,
                expected_generation=generation
                if expected_generation is None
                else expected_generation,
            )
        except Exception as exc:
            self._record_failure(attempt, "transaction", exc)
            _discard_prepared(prepared)
            raise
        self._activate_runtime(attempt, result, prepared)
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
        # An unverified actor is rejected before any work and leaves no ledger row.
        self.control.require_reviewer_actor(actor)
        attempt = _Attempt(
            action="rollback",
            actor=actor,
            reason=reason,
            subject_id=expected_deployment_id or "active-pointer",
            candidate_id=None,
            expected_deployment_id=expected_deployment_id,
            expected_generation=expected_generation,
        )
        try:
            current, generation = self.control.active()
            if current is None:
                raise TransitionError("there is no active deployment")
            attempt = replace(attempt, subject_id=current.deployment_id)
            if expected_generation is not None and (
                generation != expected_generation or current.deployment_id != expected_deployment_id
            ):
                raise ConflictError("active deployment changed concurrently")
            previous = self.control.previous_target(current)
            attempt = replace(attempt, candidate_id=previous.candidate_id)
            prepared = self._verify_candidate(previous.candidate_id)
        except Exception as exc:
            self._record_failure(attempt, "preactivation", exc)
            raise
        if expected_generation is None:
            attempt = replace(
                attempt,
                expected_deployment_id=current.deployment_id,
                expected_generation=generation,
            )
        try:
            result = self.control.activate(
                previous.candidate_id,
                actor=actor,
                reason=reason,
                action="rollback",
                expected_deployment_id=attempt.expected_deployment_id,
                expected_generation=generation
                if expected_generation is None
                else expected_generation,
            )
        except Exception as exc:
            self._record_failure(attempt, "transaction", exc)
            _discard_prepared(prepared)
            raise
        self._activate_runtime(attempt, result, prepared)
        self._mirror_activation(result)
        return result
