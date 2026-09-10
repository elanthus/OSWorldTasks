"""Durable host for caller-dispatched v5 policy episodes.

The host deliberately reuses :class:`pixelgym.grounding.v5.runner.V5Runner` for the
provider-attempt, response-persistence, reducer, parser, checkpoint, retry, and recovery
transaction.  Only the final dispatch boundary is replaced: a validated action becomes a
durable intent and the next call reports its environment result.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast

from pixelgym.actions import ActionType, InvalidActionError, build_action_space, validate_action
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    CallCaps,
    PolicyManifest,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.journal import (
    CallCapExceededError,
    JournalEvent,
    V5AttemptJournal,
)
from pixelgym.grounding.v5.runner import (
    DeadlineExecutor,
    InjectedInterruption,
    PolicyVisibleResult,
    ProviderTransport,
    V5Runner,
)
from pixelgym.grounding.v5.runner import (
    StatefulPolicyPackage as RuntimePolicy,
)
from pixelgym.platform.fingerprints import canonical_json_bytes
from pixelgym.platform.stateful_contracts import (
    MAX_TASK_INSTRUCTION_CHARS,
    SESSION_SCHEMA_VERSION,
    EpisodeClosedRecord,
    EpisodeSessionState,
    EpisodeStepRecord,
    IntentStatus,
    ReportedResult,
    SealedFailure,
    ServedAction,
    ServingIdentity,
    SessionResumePhase,
    StatefulPolicyPackage,
    StepCheckpoints,
    TerminalClassification,
)


class ServingEpisodeError(RuntimeError):
    """Base class for stateful serving host failures."""


class EpisodeNotFoundError(ServingEpisodeError):
    pass


class EpisodeEndedError(ServingEpisodeError):
    pass


class IntentReferenceError(ServingEpisodeError):
    pass


class SessionConflictError(ServingEpisodeError):
    pass


class DeploymentAttemptCapError(ServingEpisodeError):
    pass


@dataclass(frozen=True)
class ServingActResult:
    episode_id: str
    step_index: int
    intent_id: str | None
    action: ServedAction | None
    sealed_failure: SealedFailure | None
    attempt_count: int
    identity: ServingIdentity

    def __post_init__(self) -> None:
        if (self.intent_id is None) != (self.action is None):
            raise ValueError("intent_id and action are returned together")
        if (self.sealed_failure is None) == (self.intent_id is None):
            raise ValueError("an act result is either an intent or a sealed failure")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "episode_id": self.episode_id,
            "step_index": self.step_index,
            "intent_id": self.intent_id,
            "action": None if self.action is None else self.action.to_dict(),
            "sealed_failure": (
                None if self.sealed_failure is None else self.sealed_failure.value
            ),
            "attempt_count": self.attempt_count,
            "identity": self.identity.to_dict(),
        }


def _decode_session(value: Mapping[str, Any]) -> EpisodeSessionState:
    fields = dict(value)
    fields["identity"] = ServingIdentity(**fields["identity"])
    if fields.get("last_action") is not None:
        fields["last_action"] = ServedAction(**fields["last_action"])
    return EpisodeSessionState(**fields)


def _decode_step_record(value: Mapping[str, Any]) -> EpisodeStepRecord:
    fields = dict(value)
    fields.pop("schema_version", None)
    fields.pop("record_kind", None)
    fields["identity"] = ServingIdentity(**fields["identity"])
    fields["previous_result"] = (
        None
        if fields["previous_result"] is None
        else ReportedResult(**fields["previous_result"])
    )
    fields["checkpoints"] = StepCheckpoints(**fields["checkpoints"])
    fields["action"] = (
        None if fields["action"] is None else ServedAction(**fields["action"])
    )
    for name in ("attempt_ids", "canonical_response_sha256s", "provider_request_ids"):
        fields[name] = tuple(fields[name])
    return EpisodeStepRecord(**fields)


def _decode_closed_record(value: Mapping[str, Any]) -> EpisodeClosedRecord:
    fields = dict(value)
    fields.pop("schema_version", None)
    fields.pop("record_kind", None)
    fields["identity"] = ServingIdentity(**fields["identity"])
    fields["final_result"] = (
        None
        if fields["final_result"] is None
        else ReportedResult(**fields["final_result"])
    )
    return EpisodeClosedRecord(**fields)


def _is_post_dispatch_completion(
    existing_payload: bytes, updated: EpisodeStepRecord
) -> bool:
    existing = _decode_step_record(json.loads(existing_payload))
    if (
        existing.checkpoints.post_dispatch is not None
        or updated.checkpoints.post_dispatch is None
    ):
        return False
    completed = replace(
        existing,
        checkpoints=replace(
            existing.checkpoints,
            post_dispatch=updated.checkpoints.post_dispatch,
        ),
    )
    return completed == updated


class ServingSessionStore(Protocol):
    def create(self, state: EpisodeSessionState) -> None: ...

    def get(self, episode_id: str) -> EpisodeSessionState | None: ...

    def save(
        self,
        state: EpisodeSessionState,
        *,
        expected_revision: int,
        step_record: EpisodeStepRecord | None = None,
    ) -> None: ...

    def records(self, episode_id: str) -> tuple[EpisodeStepRecord, ...]: ...


class SQLiteServingSessionStore:
    """FULL-synchronous per-deployment session state with revision CAS and step records."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS serving_sessions (
                episode_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS serving_step_records (
                episode_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                payload BLOB NOT NULL,
                PRIMARY KEY (episode_id, step_index)
            );
            """
        )

    def close(self) -> None:
        self._connection.close()

    def create(self, state: EpisodeSessionState) -> None:
        encoded = canonical_json_bytes(state.to_dict())
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    "INSERT INTO serving_sessions(episode_id, revision, payload) VALUES (?, ?, ?)",
                    (state.episode_id, state.revision, encoded),
                )
            except sqlite3.IntegrityError as exc:
                raise SessionConflictError("episode already exists") from exc

    def get(self, episode_id: str) -> EpisodeSessionState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM serving_sessions WHERE episode_id = ?", (episode_id,)
            ).fetchone()
        return None if row is None else _decode_session(json.loads(bytes(row[0])))

    def save(
        self,
        state: EpisodeSessionState,
        *,
        expected_revision: int,
        step_record: EpisodeStepRecord | None = None,
    ) -> None:
        if state.revision != expected_revision + 1:
            raise ValueError("a session save must advance the revision exactly once")
        encoded = canonical_json_bytes(state.to_dict())
        record_bytes = (
            None
            if step_record is None
            else canonical_json_bytes(step_record.to_dict())
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                updated = self._connection.execute(
                    """
                    UPDATE serving_sessions SET revision = ?, payload = ?
                    WHERE episode_id = ? AND revision = ?
                    """,
                    (state.revision, encoded, state.episode_id, expected_revision),
                )
                if updated.rowcount != 1:
                    raise SessionConflictError("serving session revision changed")
                if record_bytes is not None and step_record is not None:
                    existing = self._connection.execute(
                        """
                        SELECT payload FROM serving_step_records
                        WHERE episode_id = ? AND step_index = ?
                        """,
                        (step_record.episode_id, step_record.step_index),
                    ).fetchone()
                    if existing is None:
                        self._connection.execute(
                            """
                            INSERT INTO serving_step_records(episode_id, step_index, payload)
                            VALUES (?, ?, ?)
                            """,
                            (step_record.episode_id, step_record.step_index, record_bytes),
                        )
                    elif bytes(existing[0]) == record_bytes:
                        pass
                    elif _is_post_dispatch_completion(bytes(existing[0]), step_record):
                        self._connection.execute(
                            """
                            UPDATE serving_step_records SET payload = ?
                            WHERE episode_id = ? AND step_index = ?
                            """,
                            (record_bytes, step_record.episode_id, step_record.step_index),
                        )
                    else:
                        raise SessionConflictError("serving step record changed after commit")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def records(self, episode_id: str) -> tuple[EpisodeStepRecord, ...]:
        with self._lock:
            rows = tuple(
                self._connection.execute(
                    """
                    SELECT payload FROM serving_step_records
                    WHERE episode_id = ? ORDER BY step_index
                    """,
                    (episode_id,),
                )
            )
        return tuple(_decode_step_record(json.loads(bytes(row[0]))) for row in rows)


class _ServingTransaction(V5Runner):
    """V5 transaction whose validated-action boundary issues an external intent."""

    def __init__(
        self,
        *,
        boundary_callback: Callable[[str], None],
        **kwargs: Any,
    ) -> None:
        self._boundary_callback = boundary_callback
        self._serving_action_space = build_action_space(SCREEN_WIDTH, SCREEN_HEIGHT)
        super().__init__(**kwargs)

    def _boundary(self, name: str) -> None:
        self._boundary_callback(name)
        super()._boundary(name)

    def _seal_validated_action(
        self,
        *,
        trial_id: str,
        step_index: int,
        env: Any,
        backend: Any,
        action: dict[str, int],
        candidate_digest: str,
        post_parse_state: bytes,
    ) -> dict[str, Any]:
        del env, backend
        action_digest = self.journal.put_object(
            "sealed_action", canonical_json_bytes(action)
        )
        intent_material = {
            "episode_id": trial_id,
            "step_index": step_index,
            "candidate_digest": candidate_digest,
            "action_digest": action_digest,
            "policy_id": self.manifest.policy_id,
        }
        intent_id = "intent-" + sha256_bytes(canonical_json_bytes(intent_material))[:32]
        checkpoint_digest = self.journal.put_object("policy_checkpoint", post_parse_state)
        self.journal.append_event(
            event_key=f"{trial_id}/step-{step_index:04d}/intent_issued",
            kind="intent_issued",
            trial_id=trial_id,
            step_index=step_index,
            payload={
                **intent_material,
                "intent_id": intent_id,
                "post_parse_checkpoint_digest": checkpoint_digest,
            },
        )
        self._boundary("intent_issued")
        return {
            "classification": "intent_issued",
            "state": post_parse_state,
            "action": action,
            "intent_id": intent_id,
            "redispatched": False,
        }

    def _recover_external_intent(
        self,
        *,
        trial_id: str,
        step_index: int,
        events: Sequence[JournalEvent],
        by_kind: Mapping[str, JournalEvent],
    ) -> dict[str, Any] | None:
        del trial_id, step_index, events
        intent = by_kind.get("intent_issued")
        if intent is None:
            return None
        state = self.journal.get_object(
            intent.payload["post_parse_checkpoint_digest"],
            expected_kind="policy_checkpoint",
        )
        action = json.loads(
            self.journal.get_object(
                intent.payload["action_digest"], expected_kind="sealed_action"
            )
        )
        return {
            "classification": "intent_issued",
            "state": state,
            "action": action,
            "intent_id": intent.payload["intent_id"],
            "redispatched": False,
        }

    def _recover_external_candidate(
        self,
        *,
        trial_id: str,
        step_index: int,
        candidate_event: JournalEvent,
    ) -> dict[str, Any] | None:
        candidate = json.loads(
            self.journal.get_object(
                candidate_event.payload["candidate_digest"],
                expected_kind="parsed_action_candidate",
            )
        )
        post_parse_state = self.journal.get_object(
            candidate_event.payload["post_parse_checkpoint_digest"],
            expected_kind="policy_checkpoint",
        )
        try:
            validated = validate_action(self._serving_action_space, candidate)
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
                    "candidate_digest": candidate_event.payload["candidate_digest"],
                },
            )
            return {
                "classification": "invalid_output",
                "state": post_parse_state,
                "redispatched": False,
            }
        return self._seal_validated_action(
            trial_id=trial_id,
            step_index=step_index,
            env=None,
            backend=None,
            action={
                "action_type": validated.action_type,
                "x": validated.x,
                "y": validated.y,
                "key": validated.key,
            },
            candidate_digest=candidate_event.payload["candidate_digest"],
            post_parse_state=post_parse_state,
        )


class ServingEpisodeHost:
    """Execute and recover v5 actions while the API caller owns environment dispatch."""

    def __init__(
        self,
        *,
        session_store: ServingSessionStore,
        journal: V5AttemptJournal,
        package: StatefulPolicyPackage,
        identity: ServingIdentity,
        policy: RuntimePolicy,
        transport: ProviderTransport,
        deployment_attempt_cap: int,
        interrupt_after: str | None = None,
        deadline_executor: DeadlineExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
        episode_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if identity.policy_id != package.policy_id:
            raise ValueError("serving identity does not match the stateful package")
        if type(deployment_attempt_cap) is not int or deployment_attempt_cap <= 0:
            raise ValueError("deployment_attempt_cap must be positive")
        self.session_store = session_store
        self.journal = journal
        self.package = package
        self.identity = identity
        self.policy = policy
        self.transport = transport
        self.manifest: PolicyManifest = package.v5_manifest()
        self.deployment_attempt_cap = deployment_attempt_cap
        self.interrupt_after = interrupt_after
        self.deadline_executor = deadline_executor
        self.clock = clock or (lambda: datetime.now(UTC))
        self.episode_id_factory = episode_id_factory or (
            lambda: "ep-" + uuid.uuid4().hex
        )
        self._active_state: EpisodeSessionState | None = None
        control_per_attempt = (
            self.manifest.max_cancellation_requests_per_attempt
            + self.manifest.max_reconciliation_requests_per_attempt
        )
        self._caps = CallCaps(
            environment_action_cap=package.max_steps,
            model_attempt_cap=deployment_attempt_cap,
            provider_control_request_cap=deployment_attempt_cap * control_per_attempt,
            provider_wire_request_cap=deployment_attempt_cap * (1 + control_per_attempt),
        )

    def create_episode(self, *, task_instruction: str, client_episode_ref: str) -> EpisodeSessionState:
        if not isinstance(task_instruction, str) or not task_instruction or (
            len(task_instruction) > MAX_TASK_INSTRUCTION_CHARS
        ):
            raise ValueError("task_instruction is empty or exceeds the frozen limit")
        if not isinstance(client_episode_ref, str) or not client_episode_ref:
            raise ValueError("client_episode_ref is required")
        validate_credential_free({"task_instruction": task_instruction})
        if self.journal.call_counts()[0] >= self.deployment_attempt_cap:
            raise DeploymentAttemptCapError(
                "deployment attempt cap reached; refusing a new episode"
            )
        episode_id = self.episode_id_factory()
        state_bytes = self.policy.reset(task_instruction)
        if not isinstance(state_bytes, bytes):
            raise TypeError("policy reset must return checkpoint bytes")
        checkpoint = self.journal.put_object("policy_checkpoint", state_bytes)
        now = self._now()
        session = EpisodeSessionState(
            episode_id=episode_id,
            client_episode_ref=client_episode_ref,
            identity=self.identity,
            created_at_utc=now,
            updated_at_utc=now,
            revision=0,
            resume_phase=SessionResumePhase.INITIALIZED,
            task_instruction_sha256="sha256:" + sha256_bytes(task_instruction.encode()),
            screen=dict(self.package.screen),
            max_steps=self.package.max_steps,
            max_model_attempts_per_action=self.manifest.max_model_attempts_per_action,
            deployment_attempt_cap=self.deployment_attempt_cap,
            step_index=0,
            policy_checkpoint_sha256=checkpoint,
            policy_checkpoint_object_key=self._checkpoint_key(checkpoint),
            last_intent_id=None,
            last_intent_status=IntentStatus.NONE,
            last_action=None,
            sealed_failure=None,
            terminal_classification=None,
            model_attempts=0,
            provider_control_requests=0,
            usage=None,
            attributed_cost_usd=None,
        )
        self.session_store.create(session)
        self.journal.append_event(
            event_key=f"{episode_id}/episode_initialized",
            kind="episode_initialized",
            trial_id=episode_id,
            step_index=0,
            payload={
                "task_instruction_digest": session.task_instruction_sha256,
                "policy_checkpoint_digest": checkpoint,
            },
        )
        self._interrupt("initialized")
        return session

    def get(self, episode_id: str) -> EpisodeSessionState:
        state = self.session_store.get(episode_id)
        if state is None:
            raise EpisodeNotFoundError(f"episode {episode_id!r} does not exist")
        if state.identity != self.identity:
            raise ServingEpisodeError("episode identity does not match this host")
        return state

    def step_record(self, episode_id: str, step_index: int) -> EpisodeStepRecord:
        """Return the durable record produced by one completed ``act`` boundary."""

        self.get(episode_id)
        record = next(
            (
                item
                for item in self.session_store.records(episode_id)
                if item.step_index == step_index
            ),
            None,
        )
        if record is None:
            raise ServingEpisodeError("serving step record is unavailable")
        return record

    def close_episode(
        self,
        *,
        episode_id: str,
        final_intent_id: str | None,
        final_result: ReportedResult | None,
        final_screenshot_sha256: str | None,
        final_screenshot_object_key: str | None,
    ) -> EpisodeClosedRecord:
        """Close an episode without dispatching or asking the policy for another action.

        Final screenshot bytes are stored by the API's immutable operational store before this
        method is called.  The host receives only the verified reference, applies an outstanding
        result through the same post-dispatch reducer used by ``act``, and durably binds the
        terminal record before returning it.
        """

        if (final_intent_id is None) != (final_result is None):
            raise IntentReferenceError("final intent and result must be reported together")
        if (final_screenshot_sha256 is None) != (final_screenshot_object_key is None):
            raise ValueError("final screenshot digest and object key must be supplied together")
        if final_result is not None and (
            final_screenshot_sha256 is None
            or final_result.screenshot_sha256 != final_screenshot_sha256
        ):
            raise IntentReferenceError(
                "final result screenshot digest must match the final screenshot"
            )

        state = self.get(episode_id)
        event_key = f"{episode_id}/episode_closed"
        existing = self.journal.event(event_key)
        if existing is not None:
            record = _decode_closed_record(
                json.loads(
                    self.journal.get_object(
                        existing.payload["closed_record_digest"],
                        expected_kind="episode_closed_record",
                    )
                )
            )
            if (
                record.final_intent_id != final_intent_id
                or record.final_result != final_result
                or record.final_screenshot_sha256 != final_screenshot_sha256
                or record.final_screenshot_object_key != final_screenshot_object_key
            ):
                raise EpisodeEndedError("the episode was closed with different final input")
            if state.resume_phase is not SessionResumePhase.CLOSED:
                state = self._save_closed_state(state, record)
            return record
        if state.resume_phase is SessionResumePhase.CLOSED:
            raise ServingEpisodeError("closed episode record is missing")

        if state.resume_phase is SessionResumePhase.SEALED:
            if final_intent_id is not None:
                raise IntentReferenceError("a sealed episode has no outstanding intent")
            classification = state.terminal_classification
            if classification is None:
                raise ServingEpisodeError("sealed episode has no terminal classification")
        elif state.resume_phase is SessionResumePhase.INTENT_ISSUED:
            self._validate_outstanding_intent(state, final_intent_id, final_result)
            state = self._report_result(state, cast(ReportedResult, final_result))
            classification = self._classification_for_close(
                cast(ReportedResult, final_result)
            )
        else:
            if final_intent_id is not None:
                raise IntentReferenceError("the episode has no outstanding intent")
            classification = TerminalClassification.CLOSED_BY_CALLER

        model_attempts, control_requests = self._episode_counts(episode_id)
        record = EpisodeClosedRecord(
            episode_id=episode_id,
            identity=self.identity,
            closed_at_utc=self._now(),
            terminal_classification=classification,
            final_intent_id=final_intent_id,
            final_result=final_result,
            final_screenshot_sha256=final_screenshot_sha256,
            final_screenshot_object_key=final_screenshot_object_key,
            steps=state.step_index,
            model_attempts=model_attempts,
            provider_control_requests=control_requests,
            usage=self._episode_usage(episode_id),
            attributed_cost_usd=state.attributed_cost_usd,
        )
        record_digest = self.journal.put_object(
            "episode_closed_record", canonical_json_bytes(record.to_dict())
        )
        self.journal.append_event(
            event_key=event_key,
            kind="episode_closed",
            trial_id=episode_id,
            step_index=state.step_index,
            payload={"closed_record_digest": record_digest},
        )
        self._save_closed_state(state, record)
        return record

    def _save_closed_state(
        self, state: EpisodeSessionState, record: EpisodeClosedRecord
    ) -> EpisodeSessionState:
        return self._save_phase(
            state,
            SessionResumePhase.CLOSED,
            checkpoint_digest=state.policy_checkpoint_sha256,
            last_intent_status=(
                IntentStatus.RESULT_REPORTED
                if record.final_intent_id is not None
                else state.last_intent_status
            ),
            terminal_classification=record.terminal_classification,
            model_attempts=record.model_attempts,
            provider_control_requests=record.provider_control_requests,
            usage=record.usage,
            attributed_cost_usd=record.attributed_cost_usd,
        )

    @staticmethod
    def _classification_for_close(result: ReportedResult) -> TerminalClassification:
        if result.terminated:
            return TerminalClassification.TERMINATED
        if result.truncated:
            return TerminalClassification.TRUNCATED
        return TerminalClassification.CLOSED_BY_CALLER

    def act(
        self,
        *,
        episode_id: str,
        screenshot: bytes,
        previous_intent_id: str | None = None,
        previous_result: ReportedResult | None = None,
    ) -> ServingActResult:
        if not isinstance(screenshot, bytes) or not screenshot:
            raise ValueError("screenshot must be non-empty bytes")
        if (previous_intent_id is None) != (previous_result is None):
            raise IntentReferenceError("previous intent and result must be reported together")
        state = self.get(episode_id)
        if state.resume_phase in {SessionResumePhase.SEALED, SessionResumePhase.CLOSED}:
            raise EpisodeEndedError("the episode is already over")
        screenshot_digest = "sha256:" + sha256_bytes(screenshot)
        if previous_result is not None and (
            previous_result.screenshot_sha256 != screenshot_digest
        ):
            raise IntentReferenceError(
                "previous_result screenshot digest must match the current screenshot"
            )
        sealed_event = self.journal.event(f"{episode_id}/episode_sealed")
        if sealed_event is not None:
            self._validate_act_context_if_present(
                state, screenshot_digest, previous_intent_id, previous_result
            )
            return self._seal(
                state,
                SealedFailure(sealed_event.payload["sealed_failure"]),
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
            )

        if state.resume_phase is SessionResumePhase.INTENT_ISSUED:
            if self._matches_original_act(
                state, screenshot_digest, previous_intent_id, previous_result
            ):
                return self._existing_intent_result(
                    state,
                    screenshot_digest=screenshot_digest,
                    previous_intent_id=previous_intent_id,
                    previous_result=previous_result,
                )
            self._validate_outstanding_intent(state, previous_intent_id, previous_result)
            state = self._report_result(state, cast(ReportedResult, previous_result))
            if previous_result is not None and (
                previous_result.terminated or previous_result.truncated
            ):
                raise EpisodeEndedError("the reported result ended the environment; close it")
        elif state.resume_phase is SessionResumePhase.POST_DISPATCH:
            self._validate_report_replay(state, previous_intent_id, previous_result)
            if previous_result is not None and (
                previous_result.terminated or previous_result.truncated
            ):
                raise EpisodeEndedError("the reported result ended the environment; close it")
        elif state.step_index == 0:
            if previous_intent_id is not None:
                raise IntentReferenceError("the first act cannot report a previous intent")
        else:
            self._validate_report_replay(state, previous_intent_id, previous_result)

        self._validate_act_context_if_present(
            state, screenshot_digest, previous_intent_id, previous_result
        )

        self._active_state = state
        try:
            self._record_act_context(
                state, screenshot_digest, previous_intent_id, previous_result
            )
            if state.step_index >= state.max_steps:
                return self._seal(
                    state,
                    SealedFailure.MAX_STEPS_REACHED,
                    screenshot_digest=screenshot_digest,
                    previous_intent_id=previous_intent_id,
                    previous_result=previous_result,
                )

            step_events = self._step_events(state.episode_id, state.step_index)
            has_transaction = any(
                event.kind
                not in {"episode_initialized", "act_received", "result_reported"}
                for event in step_events
            )
            if not has_transaction:
                state = self._save_phase(
                    state,
                    SessionResumePhase.PRE_CALL,
                    checkpoint_digest=state.policy_checkpoint_sha256,
                )
                self._interrupt("pre_call")
            self._active_state = state
            transaction = _ServingTransaction(
                journal=self.journal,
                manifest=self.manifest,
                transport=self.transport,
                policy=self.policy,
                approved_caps=self._caps,
                interrupt_after=self.interrupt_after,
                deadline_executor=self.deadline_executor,
                boundary_callback=self._transaction_boundary,
            )
            if has_transaction:
                outcome = transaction.recover_step(
                    trial_id=state.episode_id,
                    step_index=state.step_index,
                    task=None,
                    backend=None,
                )
            else:
                checkpoint = self.journal.get_object(
                    state.policy_checkpoint_sha256, expected_kind="policy_checkpoint"
                )
                env = SimpleNamespace(
                    action_space=build_action_space(SCREEN_WIDTH, SCREEN_HEIGHT)
                )
                outcome = transaction._act(  # shared v5 transaction; no environment dispatch
                    trial_id=state.episode_id,
                    step_index=state.step_index,
                    env=cast(Any, env),
                    backend=cast(Any, None),
                    state=checkpoint,
                    observation=SimpleNamespace(tobytes=lambda: screenshot),
                )
        except InjectedInterruption:
            raise
        except CallCapExceededError:
            return self._seal(
                self.get(episode_id),
                SealedFailure.CAP_REACHED,
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
            )
        except RuntimeError:
            return self._seal(
                self.get(episode_id),
                SealedFailure.INFRASTRUCTURE_FAILURE,
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
            )
        except Exception:  # noqa: BLE001 - fail closed as a sealed infrastructure outcome
            return self._seal(
                self.get(episode_id),
                SealedFailure.INFRASTRUCTURE_FAILURE,
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
            )
        finally:
            self._active_state = None

        latest = self.get(episode_id)
        if outcome["classification"] == "intent_issued":
            action = self._served_action(outcome["action"])
            if latest.resume_phase is not SessionResumePhase.INTENT_ISSUED:
                intent_event = self._event(
                    latest.episode_id, latest.step_index, "intent_issued"
                )
                if intent_event is None:
                    raise ServingEpisodeError("recovered intent evidence is missing")
                latest = self._save_phase(
                    latest,
                    SessionResumePhase.INTENT_ISSUED,
                    checkpoint_digest=intent_event.payload[
                        "post_parse_checkpoint_digest"
                    ],
                    last_intent_id=outcome["intent_id"],
                    last_action=action,
                    last_intent_status=IntentStatus.ISSUED,
                )
            record = self._step_record(
                latest,
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
                intent_id=outcome["intent_id"],
                action=action,
                sealed_failure=None,
            )
            latest = self._commit_record(latest, record)
            return ServingActResult(
                episode_id=episode_id,
                step_index=latest.step_index,
                intent_id=outcome["intent_id"],
                action=action,
                sealed_failure=None,
                attempt_count=len(record.attempt_ids),
                identity=self.identity,
            )
        return self._seal(
            latest,
            self._failure_for_outcome(
                outcome, episode_id=episode_id, step_index=latest.step_index
            ),
            screenshot_digest=screenshot_digest,
            previous_intent_id=previous_intent_id,
            previous_result=previous_result,
        )

    def _transaction_boundary(self, name: str) -> None:
        state = self._active_state
        if state is None:
            return
        if name == "attempt_terminal":
            terminal = self._latest_terminal_attempt(state.episode_id, state.step_index)
            if terminal is not None:
                state = self._save_phase(
                    self.get(state.episode_id),
                    SessionResumePhase.POST_ATTEMPT,
                    checkpoint_digest=terminal.payload["post_attempt_checkpoint_digest"],
                )
                self._interrupt("post_attempt")
        elif name == "parsed_action_candidate":
            event = self._event(state.episode_id, state.step_index, "parsed_action_candidate")
            if event is not None:
                state = self._save_phase(
                    self.get(state.episode_id),
                    SessionResumePhase.POST_PARSE,
                    checkpoint_digest=event.payload["post_parse_checkpoint_digest"],
                )
                self._interrupt("post_parse")
        elif name == "intent_issued":
            event = self._event(state.episode_id, state.step_index, "intent_issued")
            if event is not None:
                action = json.loads(
                    self.journal.get_object(
                        event.payload["action_digest"], expected_kind="sealed_action"
                    )
                )
                state = self._save_phase(
                    self.get(state.episode_id),
                    SessionResumePhase.INTENT_ISSUED,
                    checkpoint_digest=event.payload["post_parse_checkpoint_digest"],
                    last_intent_id=event.payload["intent_id"],
                    last_action=self._served_action(action),
                    last_intent_status=IntentStatus.ISSUED,
                )
        self._active_state = state

    def _report_result(
        self, state: EpisodeSessionState, result: ReportedResult
    ) -> EpisodeSessionState:
        event_key = f"{state.episode_id}/step-{state.step_index:04d}/result_reported"
        existing = self.journal.event(event_key)
        if existing is None:
            intent = self._event(state.episode_id, state.step_index, "intent_issued")
            if intent is None:
                raise ServingEpisodeError("outstanding intent evidence is missing")
            prior_state = self.journal.get_object(
                intent.payload["post_parse_checkpoint_digest"],
                expected_kind="policy_checkpoint",
            )
            action = json.loads(
                self.journal.get_object(
                    intent.payload["action_digest"], expected_kind="sealed_action"
                )
            )
            visible = PolicyVisibleResult(
                screenshot_digest=result.screenshot_sha256,
                reward=result.reward,
                terminated=result.terminated,
                truncated=result.truncated,
                step_index=state.step_index,
            )
            next_state = self.policy.post_dispatch_state(prior_state, action, visible)
            checkpoint = self.journal.put_object("policy_checkpoint", next_state)
            result_digest = content_digest(result.to_dict())
            existing = self.journal.append_event(
                event_key=event_key,
                kind="result_reported",
                trial_id=state.episode_id,
                step_index=state.step_index,
                payload={
                    "intent_id": state.last_intent_id,
                    "result_digest": result_digest,
                    "post_dispatch_checkpoint_digest": checkpoint,
                },
            )
            self._interrupt("result_reported")
        elif existing.payload.get("intent_id") != state.last_intent_id or (
            existing.payload.get("result_digest") != content_digest(result.to_dict())
        ):
            raise IntentReferenceError("the reported result conflicts with durable evidence")
        record = next(
            (
                item
                for item in self.session_store.records(state.episode_id)
                if item.step_index == state.step_index
            ),
            None,
        )
        if record is None:
            raise ServingEpisodeError("outstanding intent step record is missing")
        completed_record = replace(
            record,
            checkpoints=replace(
                record.checkpoints,
                post_dispatch=existing.payload["post_dispatch_checkpoint_digest"],
            ),
        )
        updated = self._save_phase(
            state,
            SessionResumePhase.POST_DISPATCH,
            checkpoint_digest=existing.payload["post_dispatch_checkpoint_digest"],
            step_record=completed_record,
            step_index=state.step_index + 1,
            last_intent_status=IntentStatus.RESULT_REPORTED,
        )
        self._interrupt("post_dispatch")
        return updated

    def _save_phase(
        self,
        state: EpisodeSessionState,
        phase: SessionResumePhase,
        *,
        checkpoint_digest: str,
        step_record: EpisodeStepRecord | None = None,
        **changes: Any,
    ) -> EpisodeSessionState:
        updated = replace(
            state,
            revision=state.revision + 1,
            updated_at_utc=self._now(),
            resume_phase=phase,
            policy_checkpoint_sha256=checkpoint_digest,
            policy_checkpoint_object_key=self._checkpoint_key(checkpoint_digest),
            **changes,
        )
        self.session_store.save(
            updated,
            expected_revision=state.revision,
            step_record=step_record,
        )
        return updated

    def _seal(
        self,
        state: EpisodeSessionState,
        failure: SealedFailure,
        *,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> ServingActResult:
        event = self.journal.append_event(
            event_key=f"{state.episode_id}/episode_sealed",
            kind="episode_sealed",
            trial_id=state.episode_id,
            step_index=state.step_index,
            payload={"sealed_failure": failure.value},
        )
        del event
        self._interrupt("sealed_event")
        record = self._step_record(
            state,
            screenshot_digest=screenshot_digest,
            previous_intent_id=previous_intent_id,
            previous_result=previous_result,
            intent_id=None,
            action=None,
            sealed_failure=failure,
        )
        current = self.get(state.episode_id)
        if current.resume_phase is not SessionResumePhase.SEALED:
            sealed = replace(
                current,
                revision=current.revision + 1,
                updated_at_utc=self._now(),
                resume_phase=SessionResumePhase.SEALED,
                last_intent_status=IntentStatus.SEALED,
                sealed_failure=failure,
                terminal_classification=TerminalClassification(failure.value),
                model_attempts=self._episode_counts(current.episode_id)[0],
                provider_control_requests=self._episode_counts(current.episode_id)[1],
                usage=self._episode_usage(current.episode_id),
            )
            self.session_store.save(
                sealed, expected_revision=current.revision, step_record=record
            )
            current = sealed
        self._interrupt("sealed")
        return ServingActResult(
            episode_id=state.episode_id,
            step_index=state.step_index,
            intent_id=None,
            action=None,
            sealed_failure=failure,
            attempt_count=len(record.attempt_ids),
            identity=self.identity,
        )

    def _commit_record(
        self, state: EpisodeSessionState, record: EpisodeStepRecord
    ) -> EpisodeSessionState:
        model_attempts, controls = self._episode_counts(state.episode_id)
        updated = replace(
            state,
            revision=state.revision + 1,
            updated_at_utc=self._now(),
            model_attempts=model_attempts,
            provider_control_requests=controls,
            usage=self._episode_usage(state.episode_id),
        )
        self.session_store.save(
            updated, expected_revision=state.revision, step_record=record
        )
        self._interrupt("step_recorded")
        return updated

    def _step_record(
        self,
        state: EpisodeSessionState,
        *,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
        intent_id: str | None,
        action: ServedAction | None,
        sealed_failure: SealedFailure | None,
    ) -> EpisodeStepRecord:
        events = self._step_events(state.episode_id, state.step_index)
        starts = [event for event in events if event.kind == "attempt_started"]
        responses = [
            event for event in events if event.kind == "canonical_response_persisted"
        ]
        terminal = self._latest_terminal_attempt(state.episode_id, state.step_index)
        candidate = next(
            (event for event in events if event.kind == "parsed_action_candidate"), None
        )
        pre_call = (
            starts[0].payload["pre_call_checkpoint_digest"]
            if starts
            else state.policy_checkpoint_sha256
        )
        provider_ids: list[str] = []
        for response in responses:
            value = json.loads(
                self.journal.get_object(
                    response.payload["canonical_response_digest"],
                    expected_kind="canonical_provider_response",
                )
            )
            provider_ids.append(value["response_id"])
        return EpisodeStepRecord(
            episode_id=state.episode_id,
            step_index=state.step_index,
            identity=self.identity,
            recorded_at_utc=self._now(),
            screenshot_sha256=screenshot_digest,
            previous_intent_id=previous_intent_id,
            previous_result=previous_result,
            attempt_ids=tuple(event.payload["identity"] for event in starts),
            canonical_response_sha256s=tuple(
                event.payload["canonical_response_digest"] for event in responses
            ),
            checkpoints=StepCheckpoints(
                pre_call=pre_call,
                post_attempt=(
                    None
                    if terminal is None
                    else terminal.payload["post_attempt_checkpoint_digest"]
                ),
                post_parse=(
                    None
                    if candidate is None
                    else candidate.payload["post_parse_checkpoint_digest"]
                ),
                post_dispatch=None,
            ),
            intent_id=intent_id,
            action=action,
            sealed_failure=sealed_failure,
            provider_request_ids=tuple(provider_ids),
            latency_ms=None,
            usage=self._step_usage(events),
            attributed_cost_usd=None,
        )

    def _record_act_context(
        self,
        state: EpisodeSessionState,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> None:
        self.journal.append_event(
            event_key=f"{state.episode_id}/step-{state.step_index:04d}/act_received",
            kind="act_received",
            trial_id=state.episode_id,
            step_index=state.step_index,
            payload={
                "screenshot_digest": screenshot_digest,
                "previous_intent_id": previous_intent_id,
                "previous_result_digest": (
                    None
                    if previous_result is None
                    else content_digest(previous_result.to_dict())
                ),
            },
        )

    def _validate_act_context_if_present(
        self,
        state: EpisodeSessionState,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> None:
        context = self._event(state.episode_id, state.step_index, "act_received")
        if context is None:
            return
        expected = {
            "screenshot_digest": screenshot_digest,
            "previous_intent_id": previous_intent_id,
            "previous_result_digest": (
                None if previous_result is None else content_digest(previous_result.to_dict())
            ),
        }
        if context.payload != expected:
            raise IntentReferenceError("act replay differs from the durable request context")

    def _matches_original_act(
        self,
        state: EpisodeSessionState,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> bool:
        context = self._event(state.episode_id, state.step_index, "act_received")
        if context is None:
            return False
        return context.payload == {
            "screenshot_digest": screenshot_digest,
            "previous_intent_id": previous_intent_id,
            "previous_result_digest": (
                None if previous_result is None else content_digest(previous_result.to_dict())
            ),
        }

    def _existing_intent_result(
        self,
        state: EpisodeSessionState,
        *,
        screenshot_digest: str,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> ServingActResult:
        record = next(
            (
                item
                for item in self.session_store.records(state.episode_id)
                if item.step_index == state.step_index
            ),
            None,
        )
        if record is None:
            record = self._step_record(
                state,
                screenshot_digest=screenshot_digest,
                previous_intent_id=previous_intent_id,
                previous_result=previous_result,
                intent_id=state.last_intent_id,
                action=state.last_action,
                sealed_failure=None,
            )
            state = self._commit_record(state, record)
        return ServingActResult(
            episode_id=state.episode_id,
            step_index=state.step_index,
            intent_id=state.last_intent_id,
            action=state.last_action,
            sealed_failure=None,
            attempt_count=(
                len(record.attempt_ids)
                if record is not None
                else len(
                    [
                        event
                        for event in self._step_events(state.episode_id, state.step_index)
                        if event.kind == "attempt_started"
                    ]
                )
            ),
            identity=self.identity,
        )

    def _validate_outstanding_intent(
        self,
        state: EpisodeSessionState,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> None:
        if previous_intent_id != state.last_intent_id or previous_result is None:
            raise IntentReferenceError("act must report the outstanding intent exactly once")

    def _validate_report_replay(
        self,
        state: EpisodeSessionState,
        previous_intent_id: str | None,
        previous_result: ReportedResult | None,
    ) -> None:
        if previous_intent_id != state.last_intent_id or previous_result is None:
            raise IntentReferenceError("act does not match the last reported intent")
        result_event = self.journal.event(
            f"{state.episode_id}/step-{state.step_index - 1:04d}/result_reported"
        )
        if result_event is None or result_event.payload["result_digest"] != content_digest(
            previous_result.to_dict()
        ):
            raise IntentReferenceError("act result differs from the durable reported result")

    def _failure_for_outcome(
        self,
        outcome: Mapping[str, Any],
        *,
        episode_id: str,
        step_index: int,
    ) -> SealedFailure:
        failure_event = next(
            (
                event
                for event in reversed(self._step_events(episode_id, step_index))
                if event.kind == "sealed_unsuccessful_result"
            ),
            None,
        )
        if failure_event is not None:
            code = failure_event.payload.get("failure_code")
            if code == "parse_failure":
                return SealedFailure.PARSE_FAILURE
            if code == "invalid_action":
                return SealedFailure.INVALID_ACTION
        classification = outcome.get("classification")
        if classification == "request_failure" or classification == "policy_violation":
            return SealedFailure.REQUEST_FAILURE
        return SealedFailure.INFRASTRUCTURE_FAILURE

    def _episode_counts(self, episode_id: str) -> tuple[int, int]:
        events = self.journal.events(episode_id)
        return (
            sum(event.kind == "attempt_started" for event in events),
            sum(event.kind == "provider_control_request_reserved" for event in events),
        )

    def _episode_usage(self, episode_id: str) -> dict[str, float] | None:
        return self._step_usage(self.journal.events(episode_id))

    @staticmethod
    def _step_usage(events: Sequence[JournalEvent]) -> dict[str, float] | None:
        totals: dict[str, float] = {}
        for event in events:
            if event.kind != "attempt_completed":
                continue
            usage = event.payload.get("usage")
            if not isinstance(usage, Mapping):
                continue
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[str(key)] = totals.get(str(key), 0.0) + float(value)
        return totals or None

    def _latest_terminal_attempt(
        self, episode_id: str, step_index: int
    ) -> JournalEvent | None:
        terminal_kinds = {
            "attempt_completed",
            "confirmed_cancellation",
            "confirmed_no_response_timeout",
            "unknown_outcome_infrastructure_failure",
        }
        return next(
            (
                event
                for event in reversed(self._step_events(episode_id, step_index))
                if event.kind in terminal_kinds
            ),
            None,
        )

    def _event(
        self, episode_id: str, step_index: int, kind: str
    ) -> JournalEvent | None:
        return next(
            (
                event
                for event in self._step_events(episode_id, step_index)
                if event.kind == kind
            ),
            None,
        )

    def _step_events(self, episode_id: str, step_index: int) -> tuple[JournalEvent, ...]:
        return tuple(
            event
            for event in self.journal.events(episode_id)
            if event.step_index == step_index
        )

    @staticmethod
    def _served_action(action: Mapping[str, int]) -> ServedAction:
        action_type = ActionType(action["action_type"])
        if action_type is ActionType.NOOP:
            return ServedAction("NOOP")
        if action_type is ActionType.CLICK:
            return ServedAction("CLICK", x=action["x"], y=action["y"])
        return ServedAction("KEY", key=action["key"])

    @staticmethod
    def _checkpoint_key(digest: str) -> str:
        return "v5-attempt-journal/policy-checkpoints/" + digest.removeprefix("sha256:")

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("serving clock must return a timezone-aware timestamp")
        return value.astimezone(UTC).isoformat()

    def _interrupt(self, boundary: str) -> None:
        if self.interrupt_after == boundary:
            raise InjectedInterruption(boundary)
