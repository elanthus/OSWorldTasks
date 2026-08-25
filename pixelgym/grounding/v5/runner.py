"""Stateful, journaled v5 policy runner with bounded provider settlement."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pixelgym.actions import InvalidActionError, validate_action
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    AttemptIdentity,
    CallCaps,
    PolicyManifest,
    V5Task,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.journal import (
    ControlRequestKind,
    TerminalAttemptKind,
    V5AttemptJournal,
)
from pixelgym.grounding.v5.resume import decode_resume_record
from pixelgym.serialization import canonical_json_bytes
from pixelgym.task_spec import TaskSpec


@dataclass(frozen=True)
class TransportOutcome:
    status: Literal["response", "pre_send_failure", "deadline", "unknown"]
    response: dict[str, Any] | None = None
    failure_code: str | None = None


class ProviderTransport(Protocol):
    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome: ...
    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]: ...
    def reconcile(
        self, *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome: ...


class StatefulPolicyPackage(Protocol):
    def reset(self, task_instruction: str) -> bytes: ...
    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]: ...
    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes: ...
    def failure_state(self, state: bytes, failure_code: str) -> bytes: ...
    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]: ...
    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes: ...
    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: dict[str, Any]
    ) -> bytes: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class BoundedCallResult:
    value: object | None
    timed_out: bool


class DeadlineExecutor(Protocol):
    def execute(self, call: Callable[[], object], *, timeout_seconds: float) -> BoundedCallResult: ...


class DaemonDeadlineExecutor:
    """Bound a blocking adapter call without letting it hold runner progress.

    Provider policies run in their own OS sandbox process in a real evaluation;
    the daemon thread is the local no-cost boundary. A timed-out call is never
    reused for another send, while cancellation/reconciliation use separately
    bounded calls.
    """

    def execute(self, call: Callable[[], object], *, timeout_seconds: float) -> BoundedCallResult:
        if timeout_seconds <= 0:
            raise ValueError("bounded call timeout must be positive")
        completed = threading.Event()
        outcome: list[object] = []
        failure: list[Exception] = []

        def invoke() -> None:
            try:
                outcome.append(call())
            except Exception as exc:  # noqa: BLE001 - re-raised unchanged on the caller thread
                failure.append(exc)
            finally:
                completed.set()

        thread = threading.Thread(target=invoke, name="v5-bounded-provider-call", daemon=True)
        thread.start()
        if not completed.wait(timeout_seconds):
            return BoundedCallResult(None, True)
        if failure:
            raise failure[0]
        return BoundedCallResult(outcome[0], False)


@dataclass(frozen=True)
class EpisodeResult:
    trial_id: str
    task_id: str
    success: bool
    classification: str
    environment_actions: int
    model_attempts: int
    provider_control_requests: int
    provider_wire_requests: int
    final_policy_checkpoint_digest: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class InjectedInterruption(RuntimeError):
    """No-cost test interruption raised immediately after a named durable boundary."""

    def __init__(self, boundary: str) -> None:
        super().__init__(f"injected interruption after {boundary}")
        self.boundary = boundary


class ScriptedTransport:
    """No-network fake transport; outcomes are consumed in declared order."""

    def __init__(self, outcomes: list[TransportOutcome] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.model_requests: list[dict[str, Any]] = []
        self.control_requests: list[tuple[str, str]] = []
        self._produced: dict[str, dict[str, Any]] = {}

    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del deadline_seconds
        self.model_requests.append({"idempotency_key": idempotency_key, "request": request})
        if self.outcomes:
            outcome = self.outcomes.pop(0)
        else:
            action = request["scripted_action"]
            outcome = TransportOutcome(
                "response",
                {
                    "response_id": f"fake-{len(self.model_requests)}",
                    "model": "no-cost-scripted-policy",
                    "content": json.dumps(action, sort_keys=True, separators=(",", ":")),
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        if outcome.response is not None:
            self._produced[idempotency_key] = outcome.response
        return outcome

    def cancel(self, *, idempotency_key: str, mode: str) -> Literal["cancelled", "unknown"]:
        self.control_requests.append(("cancel", idempotency_key))
        del mode
        return "cancelled" if idempotency_key not in self._produced else "unknown"

    def reconcile(
        self, *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del deadline_seconds
        self.control_requests.append(("reconcile", idempotency_key))
        response = self._produced.get(idempotency_key)
        if response is None:
            return TransportOutcome("unknown", failure_code="outcome_not_recoverable")
        return TransportOutcome("response", response)


class ScriptedStatefulPolicy:
    """Deterministic no-cost package used to prove state and resume semantics."""

    def __init__(self, actions: tuple[dict[str, int], ...]) -> None:
        self.actions = actions
        self.reset_calls = 0
        self.reduce_calls = 0
        self.parse_calls = 0
        self.closed = False

    def reset(self, task_instruction: str) -> bytes:
        self.reset_calls += 1
        return canonical_json_bytes(
            {"instruction": task_instruction, "action_index": 0, "history": []}
        )

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        index = value["action_index"]
        if index >= len(self.actions):
            raise RuntimeError("scripted policy has no remaining action")
        return {
            "screenshot_digest": "sha256:" + sha256_bytes(screenshot),
            "scripted_action": self.actions[index],
        }

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        self.reduce_calls += 1
        value = json.loads(state)
        response = json.loads(canonical_response)
        value["history"].append({"response_digest": content_digest(response)})
        return canonical_json_bytes(value)

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        value = json.loads(state)
        value["history"].append({"failure_code": failure_code})
        return canonical_json_bytes(value)

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        self.parse_calls += 1
        del state
        response = json.loads(canonical_response)
        candidate = json.loads(response["content"])
        if not isinstance(candidate, dict):
            raise TypeError("provider content must be one action object")
        return candidate

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        value = json.loads(state)
        value["pending_action_digest"] = content_digest(candidate)
        return canonical_json_bytes(value)

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: dict[str, Any]
    ) -> bytes:
        value = json.loads(state)
        value.pop("pending_action_digest", None)
        value["history"].append(
            {"action_digest": content_digest(action), "result_digest": content_digest(result)}
        )
        value["action_index"] += 1
        return canonical_json_bytes(value)

    def close(self) -> None:
        self.closed = True


class V5Runner:
    def __init__(
        self,
        *,
        journal: V5AttemptJournal,
        manifest: PolicyManifest,
        transport: ProviderTransport,
        policy: StatefulPolicyPackage,
        approved_caps: CallCaps,
        interrupt_after: str | None = None,
        deadline_executor: DeadlineExecutor | None = None,
    ) -> None:
        self.journal = journal
        self.manifest = manifest
        self.transport = transport
        self.policy = policy
        self.approved_caps = approved_caps
        self.interrupt_after = interrupt_after
        self.deadline_executor = deadline_executor or DaemonDeadlineExecutor()

    @property
    def model_attempts(self) -> int:
        """Run-wide model reservations, including those made by prior processes."""

        return self.journal.call_counts()[0]

    @property
    def control_requests(self) -> int:
        """Run-wide control reservations, including unknown post-crash outcomes."""

        return self.journal.call_counts()[1]

    def run(self, *, trial_id: str, task: V5Task, backend: V5FakeBackend | None = None) -> EpisodeResult:
        if not trial_id:
            raise ValueError("trial_id is required")
        backend = backend or V5FakeBackend()
        self._preflight(task, backend)
        env = PixelGuiEnv(
            backend,
            instruction=task.instruction,
            max_episode_steps=task.max_episode_steps,
        )
        state = self.policy.reset(task.instruction)
        starting_model_attempts = self.model_attempts
        starting_control_requests = self.control_requests
        environment_actions = 0
        classification = "incomplete"
        success = False
        try:
            observation, info = env.reset(seed=task.seed)
            initial_digest = self.journal.put_object("screenshot", observation.tobytes())
            initial_checkpoint = backend.checkpoint()
            initial_checkpoint_digest = self.journal.put_object(
                "environment_checkpoint", initial_checkpoint
            )
            initial_resume = backend.environment_resume_record(step_count=0)
            initial_resume_digest = self.journal.put_object(
                "environment_resume_record", canonical_json_bytes(initial_resume.to_dict())
            )
            self.journal.append_event(
                event_key=f"{trial_id}/initial_screenshot",
                kind="initial_screenshot",
                trial_id=trial_id,
                step_index=0,
                payload={
                    "screenshot_digest": initial_digest,
                    "task_id": info["task_id"],
                    "environment_checkpoint_digest": initial_checkpoint_digest,
                    "environment_resume_digest": initial_resume_digest,
                },
            )
            for step_index in range(task.max_episode_steps):
                outcome = self._act(
                    trial_id=trial_id,
                    step_index=step_index,
                    env=env,
                    backend=backend,
                    state=state,
                    observation=observation,
                )
                if outcome["classification"] != "dispatched":
                    classification = str(outcome["classification"])
                    state = outcome["state"]
                    break
                environment_actions += 1
                state = outcome["state"]
                observation = outcome["observation"]
                if outcome["terminated"]:
                    success = True
                    classification = "success_termination"
                    break
                if outcome["truncated"]:
                    classification = "step_limit_truncation"
                    break
            else:  # pragma: no cover - PixelGuiEnv truncates on the final action
                classification = "step_limit_truncation"
        finally:
            self.policy.close()
            env.close()
        return EpisodeResult(
            trial_id=trial_id,
            task_id=task.task_id,
            success=success,
            classification=classification,
            environment_actions=environment_actions,
            model_attempts=self.model_attempts - starting_model_attempts,
            provider_control_requests=self.control_requests - starting_control_requests,
            provider_wire_requests=(
                self.model_attempts
                - starting_model_attempts
                + self.control_requests
                - starting_control_requests
            ),
            final_policy_checkpoint_digest="sha256:" + sha256_bytes(state),
        )

    def _act(
        self,
        *,
        trial_id: str,
        step_index: int,
        env: PixelGuiEnv,
        backend: V5FakeBackend,
        state: bytes,
        observation: Any,
    ) -> dict[str, Any]:
        screenshot_bytes = observation.tobytes()
        request = self.policy.build_request(state, screenshot_bytes)
        validate_credential_free(request)
        request_bytes = canonical_json_bytes(request)
        identity = AttemptIdentity(trial_id, step_index, 0)
        idempotency_key = content_digest({"attempt": identity.key, "policy": self.manifest.policy_id})
        _reservation, created = self.journal.reserve_attempt_started(
            identity,
            provider_endpoint_identity=self.manifest.sandbox.provider_endpoint,
            request_digest="sha256:" + sha256_bytes(request_bytes),
            idempotency_key=idempotency_key,
            model_attempt_reservation=1,
            control_request_reservation=(
                self.manifest.max_cancellation_requests_per_attempt
                + self.manifest.max_reconciliation_requests_per_attempt
            ),
            pre_call_checkpoint=state,
            approved_caps=self.approved_caps,
        )
        if not created:
            raise RuntimeError("model attempt is already durably reserved")
        self._boundary("attempt_started")
        bounded_send = self.deadline_executor.execute(
            lambda: self.transport.send(
                request,
                idempotency_key=idempotency_key,
                deadline_seconds=self.manifest.request_deadline_seconds,
            ),
            timeout_seconds=self.manifest.request_deadline_seconds,
        )
        transport_outcome = (
            TransportOutcome("deadline", failure_code="runner_request_deadline")
            if bounded_send.timed_out
            else bounded_send.value
        )
        if not isinstance(transport_outcome, TransportOutcome):
            raise TypeError("provider transport returned an invalid outcome")
        self._boundary("provider_receipt")
        settled = self._settle(identity, idempotency_key, transport_outcome, state)
        if settled["response"] is None:
            return {"classification": settled["classification"], "state": settled["state"]}
        canonical_response = settled["response"]
        post_attempt_state = settled["state"]
        try:
            self._boundary("before_parse")
            candidate = self.policy.parse(canonical_response, post_attempt_state)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.journal.append_event(
                event_key=f"{identity.key}/sealed_parser_failure",
                kind="sealed_unsuccessful_result",
                trial_id=trial_id,
                step_index=step_index,
                attempt_index=0,
                payload={
                    "failure_code": "parse_failure",
                    "sanitized_reason": type(exc).__name__,
                    "parser_version": self.manifest.parser_version,
                    "policy_checkpoint_digest": "sha256:" + sha256_bytes(post_attempt_state),
                },
            )
            self._boundary("parser_failure")
            return {"classification": "invalid_output", "state": post_attempt_state}
        post_parse_state = self.policy.post_parse_state(post_attempt_state, candidate)
        candidate_bytes = canonical_json_bytes(candidate)
        candidate_digest = self.journal.put_object("parsed_action_candidate", candidate_bytes)
        checkpoint_digest = self.journal.put_object("policy_checkpoint", post_parse_state)
        self.journal.append_event(
            event_key=f"{trial_id}/step-{step_index:04d}/parsed_action_candidate",
            kind="parsed_action_candidate",
            trial_id=trial_id,
            step_index=step_index,
            payload={
                "candidate_digest": candidate_digest,
                "attempt_identities": [identity.key],
                "parser_version": self.manifest.parser_version,
                "post_parse_checkpoint_digest": checkpoint_digest,
            },
        )
        self._boundary("parsed_action_candidate")
        try:
            validated = validate_action(env.action_space, candidate)
        except InvalidActionError as exc:
            self.journal.append_event(
                event_key=f"{trial_id}/step-{step_index:04d}/sealed_invalid_candidate",
                kind="sealed_unsuccessful_result",
                trial_id=trial_id,
                step_index=step_index,
                payload={
                    "failure_code": "invalid_action",
                    "sanitized_reason": type(exc).__name__,
                    "action_schema_version": "pixelgym-action-v1",
                    "candidate_digest": candidate_digest,
                },
            )
            return {"classification": "invalid_output", "state": post_parse_state}
        action = {
            "action_type": validated.action_type,
            "x": validated.x,
            "y": validated.y,
            "key": validated.key,
        }
        resume_checkpoint = backend.checkpoint()
        resume_checkpoint_digest = self.journal.put_object(
            "environment_checkpoint", resume_checkpoint
        )
        resume_record = backend.environment_resume_record(step_count=step_index)
        resume_digest = self.journal.put_object(
            "environment_resume_record", canonical_json_bytes(resume_record.to_dict())
        )
        action_digest = self.journal.put_object("sealed_action", canonical_json_bytes(action))
        intent_payload = {
            "candidate_digest": candidate_digest,
            "action_digest": action_digest,
            "environment_resume_digest": resume_digest,
            "environment_checkpoint_digest": resume_checkpoint_digest,
        }
        intent_digest = content_digest(intent_payload)
        self.journal.append_event(
            event_key=f"{trial_id}/step-{step_index:04d}/sealed_action_intent",
            kind="sealed_action_intent",
            trial_id=trial_id,
            step_index=step_index,
            payload={**intent_payload, "sealed_intent_digest": intent_digest},
        )
        self._boundary("sealed_action_intent")
        return self._dispatch_intent(
            trial_id=trial_id,
            step_index=step_index,
            env=env,
            backend=backend,
            action=action,
            intent_digest=intent_digest,
            post_parse_state=post_parse_state,
        )

    def _dispatch_intent(
        self,
        *,
        trial_id: str,
        step_index: int,
        env: PixelGuiEnv,
        backend: V5FakeBackend,
        action: dict[str, int],
        intent_digest: str,
        post_parse_state: bytes,
    ) -> dict[str, Any]:
        self._boundary("before_dispatch")
        self.journal.append_event(
            event_key=f"{trial_id}/step-{step_index:04d}/dispatch_started",
            kind="dispatch_started",
            trial_id=trial_id,
            step_index=step_index,
            payload={
                "sealed_intent_digest": intent_digest,
                "backend_acceptance": "not_yet_invoked",
            },
        )
        self._boundary("dispatch_started")
        next_observation, reward, terminated, truncated, _info = env.step(action)
        self._boundary("backend_accepted")
        result_record = {
            "screenshot_digest": self.journal.put_object(
                "screenshot", next_observation.tobytes()
            ),
            "reward": reward,
            "terminated": terminated,
            "truncated": truncated,
            "diagnostic": backend.read_privileged_diagnostic(),
        }
        next_state = self.policy.post_dispatch_state(post_parse_state, action, result_record)
        next_checkpoint_digest = self.journal.put_object("policy_checkpoint", next_state)
        environment_checkpoint_digest = self.journal.put_object(
            "environment_checkpoint", backend.checkpoint()
        )
        post_dispatch_resume = backend.environment_resume_record(step_count=step_index + 1)
        post_dispatch_resume_digest = self.journal.put_object(
            "environment_resume_record", canonical_json_bytes(post_dispatch_resume.to_dict())
        )
        result_digest = content_digest(result_record)
        self.journal.append_event(
            event_key=f"{trial_id}/step-{step_index:04d}/dispatch_committed",
            kind="dispatch_committed",
            trial_id=trial_id,
            step_index=step_index,
            payload={
                "sealed_intent_digest": intent_digest,
                "backend_acceptance": "accepted_once",
                "commit_result_digest": result_digest,
                "post_dispatch_checkpoint_digest": next_checkpoint_digest,
                "environment_checkpoint_digest": environment_checkpoint_digest,
                "environment_resume_digest": post_dispatch_resume_digest,
                **result_record,
            },
        )
        self._boundary("dispatch_committed")
        return {
            "classification": "dispatched",
            "state": next_state,
            "observation": next_observation,
            "terminated": terminated,
            "truncated": truncated,
        }

    def _settle(
        self,
        identity: AttemptIdentity,
        idempotency_key: str,
        outcome: TransportOutcome,
        state: bytes,
    ) -> dict[str, Any]:
        if outcome.status == "response" and outcome.response is not None:
            event, response_bytes = self.journal.persist_canonical_response(identity, outcome.response)
            self._boundary("canonical_response_persisted")
            post_state = self.policy.reduce_state(state, response_bytes)
            self.journal.seal_attempt_terminal(
                identity,
                kind="attempt_completed",
                post_attempt_checkpoint=post_state,
                response_digest=event.payload["canonical_response_digest"],
                usage=outcome.response["usage"],
            )
            self._boundary("attempt_terminal")
            return {"response": response_bytes, "state": post_state, "classification": "response"}
        failure_code = outcome.failure_code or outcome.status
        if outcome.status == "deadline" and self.manifest.max_cancellation_requests_per_attempt:
            if not self._reserve_control_request(identity, "cancel"):
                raise RuntimeError("cancellation request outcome is already unknown")
            bounded_cancellation = self.deadline_executor.execute(
                lambda: self.transport.cancel(
                    idempotency_key=idempotency_key, mode=self.manifest.cancellation_mode
                ),
                timeout_seconds=max(0.001, self.manifest.reconciliation_deadline_seconds),
            )
            cancellation = (
                "unknown" if bounded_cancellation.timed_out else bounded_cancellation.value
            )
            if cancellation == "cancelled":
                post_state = self.policy.failure_state(state, "confirmed_cancellation")
                self.journal.seal_attempt_terminal(
                    identity,
                    kind="confirmed_cancellation",
                    post_attempt_checkpoint=post_state,
                    failure_code="request_deadline",
                )
                self._boundary("attempt_terminal")
                return {
                    "response": None,
                    "state": post_state,
                    "classification": "request_failure",
                }
        if self.manifest.max_reconciliation_requests_per_attempt:
            if not self._reserve_control_request(identity, "reconcile"):
                raise RuntimeError("reconciliation request outcome is already unknown")
            bounded_reconciliation = self.deadline_executor.execute(
                lambda: self.transport.reconcile(
                    idempotency_key=idempotency_key,
                    deadline_seconds=self.manifest.reconciliation_deadline_seconds,
                ),
                timeout_seconds=max(0.001, self.manifest.reconciliation_deadline_seconds),
            )
            reconciled = (
                TransportOutcome("unknown", failure_code="reconciliation_deadline")
                if bounded_reconciliation.timed_out
                else bounded_reconciliation.value
            )
            if not isinstance(reconciled, TransportOutcome):
                raise TypeError("provider reconciliation returned an invalid outcome")
            if reconciled.status == "response" and reconciled.response is not None:
                return self._settle(identity, idempotency_key, reconciled, state)
            failure_code = reconciled.failure_code or "unknown_outcome"
        post_state = self.policy.failure_state(state, failure_code)
        kind: TerminalAttemptKind = (
            "confirmed_no_response_timeout"
            if outcome.status == "pre_send_failure"
            else "unknown_outcome_infrastructure_failure"
        )
        self.journal.seal_attempt_terminal(
            identity,
            kind=kind,
            post_attempt_checkpoint=post_state,
            failure_code=failure_code,
        )
        self._boundary("attempt_terminal")
        return {
            "response": None,
            "state": post_state,
            "classification": (
                "request_failure" if kind == "confirmed_no_response_timeout" else "infrastructure_failure"
            ),
        }

    def _preflight(self, task: V5Task, backend: V5FakeBackend) -> None:
        validate_credential_free(task.canonical_dict())
        validate_credential_free(self.manifest.identity_fields())
        validate_credential_free({"app_url": backend.app_url})
        if self.approved_caps.environment_action_cap < task.max_episode_steps:
            raise RuntimeError("approved environment-action cap is below the assigned task bound")

    def _reserve_control_request(
        self, identity: AttemptIdentity, request_kind: ControlRequestKind
    ) -> bool:
        _reservation, created = self.journal.reserve_control_request(
            identity,
            request_kind=request_kind,
            approved_caps=self.approved_caps,
        )
        return created

    def _boundary(self, name: str) -> None:
        if self.interrupt_after == name:
            raise InjectedInterruption(name)

    def recover_step(
        self,
        *,
        trial_id: str,
        step_index: int,
        task: V5Task,
        backend: V5FakeBackend,
    ) -> dict[str, Any]:
        """Recover one interrupted action without duplicating a request or dispatch."""

        events = [
            event
            for event in self.journal.events(trial_id)
            if event.step_index == step_index
        ]
        by_kind = {event.kind: event for event in events}
        if "dispatch_committed" in by_kind:
            event = by_kind["dispatch_committed"]
            state = self.journal.get_object(
                event.payload["post_dispatch_checkpoint_digest"],
                expected_kind="policy_checkpoint",
            )
            return {"classification": "already_committed", "state": state, "redispatched": False}
        if "dispatch_started" in by_kind:
            return {
                "classification": "infrastructure_failure",
                "reason": "dispatch_started_without_commit",
                "redispatched": False,
            }
        if "sealed_unsuccessful_result" in by_kind:
            return {
                "classification": "sealed_unsuccessful_result",
                "redispatched": False,
            }
        if "sealed_action_intent" in by_kind:
            intent = by_kind["sealed_action_intent"]
            candidate_event = by_kind["parsed_action_candidate"]
            post_parse_state = self.journal.get_object(
                candidate_event.payload["post_parse_checkpoint_digest"],
                expected_kind="policy_checkpoint",
            )
            action = json.loads(
                self.journal.get_object(intent.payload["action_digest"])
            )
            checkpoint = self.journal.get_object(
                intent.payload["environment_checkpoint_digest"],
                expected_kind="environment_checkpoint",
            )
            backend.restore(checkpoint)
            resume_record = decode_resume_record(
                self.journal.get_object(
                    intent.payload["environment_resume_digest"],
                    expected_kind="environment_resume_record",
                )
            )
            backend.verify_resume_record(resume_record, step_count=step_index)
            env = self._restored_env(task, backend, step_count=step_index)
            outcome = self._dispatch_intent(
                trial_id=trial_id,
                step_index=step_index,
                env=env,
                backend=backend,
                action=action,
                intent_digest=intent.payload["sealed_intent_digest"],
                post_parse_state=post_parse_state,
            )
            outcome["redispatched"] = True
            return outcome
        if "parsed_action_candidate" in by_kind:
            self._restore_current_environment(
                trial_id=trial_id,
                step_index=step_index,
                backend=backend,
            )
            candidate_event = by_kind["parsed_action_candidate"]
            candidate = json.loads(
                self.journal.get_object(candidate_event.payload["candidate_digest"])
            )
            env = self._restored_env(task, backend, step_count=step_index)
            try:
                validated = validate_action(env.action_space, candidate)
            except InvalidActionError:
                self.journal.append_event(
                    event_key=f"{trial_id}/step-{step_index:04d}/sealed_invalid_candidate",
                    kind="sealed_unsuccessful_result",
                    trial_id=trial_id,
                    step_index=step_index,
                    payload={
                        "failure_code": "invalid_action",
                        "candidate_digest": candidate_event.payload["candidate_digest"],
                    },
                )
                return {"classification": "invalid_output", "redispatched": False}
            action = {
                "action_type": validated.action_type,
                "x": validated.x,
                "y": validated.y,
                "key": validated.key,
            }
            post_parse_state = self.journal.get_object(
                candidate_event.payload["post_parse_checkpoint_digest"],
                expected_kind="policy_checkpoint",
            )
            checkpoint = backend.checkpoint()
            checkpoint_digest = self.journal.put_object("environment_checkpoint", checkpoint)
            resume_record = backend.environment_resume_record(step_count=step_index)
            resume_digest = self.journal.put_object(
                "environment_resume_record", canonical_json_bytes(resume_record.to_dict())
            )
            action_digest = self.journal.put_object("sealed_action", canonical_json_bytes(action))
            payload = {
                "candidate_digest": candidate_event.payload["candidate_digest"],
                "action_digest": action_digest,
                "environment_resume_digest": resume_digest,
                "environment_checkpoint_digest": checkpoint_digest,
            }
            intent_digest = content_digest(payload)
            self.journal.append_event(
                event_key=f"{trial_id}/step-{step_index:04d}/sealed_action_intent",
                kind="sealed_action_intent",
                trial_id=trial_id,
                step_index=step_index,
                payload={**payload, "sealed_intent_digest": intent_digest},
            )
            return self.recover_step(
                trial_id=trial_id,
                step_index=step_index,
                task=task,
                backend=backend,
            )
        canonical_event = by_kind.get("canonical_response_persisted")
        terminal = next(
            (event for event in events if event.kind.endswith("failure") or event.kind in {
                "attempt_completed",
                "confirmed_cancellation",
                "confirmed_no_response_timeout",
            }),
            None,
        )
        if terminal is not None and terminal.kind != "attempt_completed":
            return {"classification": "infrastructure_failure", "redispatched": False}
        if canonical_event is not None:
            response_bytes = self.journal.get_object(
                canonical_event.payload["canonical_response_digest"],
                expected_kind="canonical_provider_response",
            )
            if terminal is None:
                started = by_kind["attempt_started"]
                pre_state = self.journal.get_object(
                    started.payload["pre_call_checkpoint_digest"],
                    expected_kind="policy_checkpoint",
                )
                post_state = self.policy.reduce_state(pre_state, response_bytes)
                identity = AttemptIdentity(trial_id, step_index, 0)
                self.journal.seal_attempt_terminal(
                    identity,
                    kind="attempt_completed",
                    post_attempt_checkpoint=post_state,
                    response_digest=canonical_event.payload["canonical_response_digest"],
                    usage=json.loads(response_bytes)["usage"],
                )
            else:
                post_state = self.journal.get_object(
                    terminal.payload["post_attempt_checkpoint_digest"],
                    expected_kind="policy_checkpoint",
                )
            candidate = self.policy.parse(response_bytes, post_state)
            post_parse_state = self.policy.post_parse_state(post_state, candidate)
            candidate_digest = self.journal.put_object(
                "parsed_action_candidate", canonical_json_bytes(candidate)
            )
            post_parse_digest = self.journal.put_object("policy_checkpoint", post_parse_state)
            self.journal.append_event(
                event_key=f"{trial_id}/step-{step_index:04d}/parsed_action_candidate",
                kind="parsed_action_candidate",
                trial_id=trial_id,
                step_index=step_index,
                payload={
                    "candidate_digest": candidate_digest,
                    "attempt_identities": [AttemptIdentity(trial_id, step_index, 0).key],
                    "parser_version": self.manifest.parser_version,
                    "post_parse_checkpoint_digest": post_parse_digest,
                },
            )
            return self.recover_step(
                trial_id=trial_id,
                step_index=step_index,
                task=task,
                backend=backend,
            )
        if "attempt_started" in by_kind:
            started = by_kind["attempt_started"]
            identity = AttemptIdentity(trial_id, step_index, 0)
            pre_state = self.journal.get_object(
                started.payload["pre_call_checkpoint_digest"],
                expected_kind="policy_checkpoint",
            )
            if self.manifest.max_reconciliation_requests_per_attempt == 0:
                post_state = self.policy.failure_state(pre_state, "reconciliation_disabled")
                self.journal.seal_attempt_terminal(
                    identity,
                    kind="unknown_outcome_infrastructure_failure",
                    post_attempt_checkpoint=post_state,
                    failure_code="reconciliation_disabled",
                )
                return {
                    "classification": "infrastructure_failure",
                    "reason": "reconciliation_disabled",
                    "redispatched": False,
                }
            if not self._reserve_control_request(identity, "reconcile"):
                return {
                    "classification": "infrastructure_failure",
                    "reason": "control_request_reserved_without_settlement",
                    "redispatched": False,
                }
            bounded_reconciliation = self.deadline_executor.execute(
                lambda: self.transport.reconcile(
                    idempotency_key=started.payload["idempotency_key"],
                    deadline_seconds=self.manifest.reconciliation_deadline_seconds,
                ),
                timeout_seconds=max(0.001, self.manifest.reconciliation_deadline_seconds),
            )
            reconciled = (
                TransportOutcome("unknown", failure_code="reconciliation_deadline")
                if bounded_reconciliation.timed_out
                else bounded_reconciliation.value
            )
            if not isinstance(reconciled, TransportOutcome):
                raise TypeError("provider reconciliation returned an invalid outcome")
            if reconciled.status != "response" or reconciled.response is None:
                post_state = self.policy.failure_state(
                    pre_state, reconciled.failure_code or "outcome_not_recoverable"
                )
                self.journal.seal_attempt_terminal(
                    identity,
                    kind="unknown_outcome_infrastructure_failure",
                    post_attempt_checkpoint=post_state,
                    failure_code=reconciled.failure_code or "outcome_not_recoverable",
                )
                return {
                    "classification": "infrastructure_failure",
                    "redispatched": False,
                }
            self._settle(identity, started.payload["idempotency_key"], reconciled, pre_state)
            return self.recover_step(
                trial_id=trial_id,
                step_index=step_index,
                task=task,
                backend=backend,
            )
        raise RuntimeError("no durable v5 recovery boundary exists for this step")

    def _restore_current_environment(
        self, *, trial_id: str, step_index: int, backend: V5FakeBackend
    ) -> None:
        if step_index == 0:
            event = self.journal.event(f"{trial_id}/initial_screenshot")
        else:
            event = self.journal.event(
                f"{trial_id}/step-{step_index - 1:04d}/dispatch_committed"
            )
        if event is None:
            raise RuntimeError("prior committed environment state is missing")
        checkpoint = self.journal.get_object(
            event.payload["environment_checkpoint_digest"],
            expected_kind="environment_checkpoint",
        )
        backend.restore(checkpoint)
        resume_record = decode_resume_record(
            self.journal.get_object(
                event.payload["environment_resume_digest"],
                expected_kind="environment_resume_record",
            )
        )
        backend.verify_resume_record(resume_record, step_count=step_index)

    @staticmethod
    def _restored_env(
        task: V5Task, backend: V5FakeBackend, *, step_count: int
    ) -> PixelGuiEnv:
        env = PixelGuiEnv(
            backend,
            instruction=task.instruction,
            max_episode_steps=task.max_episode_steps,
        )
        task_spec = TaskSpec.from_generated(
            task.generated_record(),
            instruction=task.instruction,
            app_url=backend.app_url,
            max_episode_steps=task.max_episode_steps,
        )
        env.restore_episode(task_spec, step_count=step_count)
        return env
