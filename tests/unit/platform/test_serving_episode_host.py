"""S3 host tests: fake execution, durable recovery, sealing, intent, and caps."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import TransportOutcome, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import (
    InjectedInterruption,
    ScriptedStatefulPolicy,
    ScriptedTransport,
)
from pixelgym.platform.serving_episode import (
    EpisodeEndedError,
    IntentReferenceError,
    ServingEpisodeError,
    ServingEpisodeHost,
    SessionConflictError,
    SQLiteServingSessionStore,
)
from pixelgym.platform.stateful_contracts import (
    EvidenceClass,
    ReportedResult,
    SealedFailure,
    SessionResumePhase,
    StatefulPolicyPackage,
)
from tests.unit.platform.stateful_fixtures import evidence, identity, v5_manifest

EPISODE_ID = "ep-" + "3" * 32
NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}
CLICK = {"action_type": 1, "x": 30, "y": 40, "key": 0}


class AlwaysRetryPolicy(ScriptedStatefulPolicy):
    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        del canonical_response
        return "scripted_retryable_response"


def _package(*, max_steps: int = 40) -> StatefulPolicyPackage:
    return StatefulPolicyPackage.build(
        manifest=v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256="9" * 64,
        dependency_lock_sha256="8" * 64,
        max_steps=max_steps,
        evidence_class=EvidenceClass.CALIBRATION,
        evidence=evidence(),
        code_revision="2" * 40,
        code_state="clean",
        source_tree_sha256="7" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )


def _result(screenshot: bytes, *, terminated: bool = False) -> ReportedResult:
    return ReportedResult(
        reward=1.0 if terminated else 0.0,
        terminated=terminated,
        truncated=False,
        screenshot_sha256="sha256:" + sha256_bytes(screenshot),
    )


def _host(
    root: Path,
    *,
    actions: tuple[dict[str, int], ...] = (NOOP, CLICK),
    transport: ScriptedTransport | None = None,
    interrupt_after: str | None = None,
    attempt_cap: int = 20,
    max_steps: int = 40,
    policy: ScriptedStatefulPolicy | None = None,
) -> ServingEpisodeHost:
    return ServingEpisodeHost(
        session_store=SQLiteServingSessionStore(root / "sessions.sqlite"),
        journal=V5AttemptJournal(root / "attempts.sqlite"),
        package=_package(max_steps=max_steps),
        identity=replace(identity(), policy_id=_package(max_steps=max_steps).policy_id),
        policy=policy or ScriptedStatefulPolicy(actions),
        transport=transport or ScriptedTransport(),
        deployment_attempt_cap=attempt_cap,
        interrupt_after=interrupt_after,
        episode_id_factory=lambda: EPISODE_ID,
    )


def test_fake_policy_serves_multiple_actions_and_applies_reported_result(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    host = _host(tmp_path, transport=transport)
    host.create_episode(task_instruction="Complete the form", client_episode_ref="client-1")

    first = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    second_screen = b"screen-1"
    second = host.act(
        episode_id=EPISODE_ID,
        screenshot=second_screen,
        previous_intent_id=first.intent_id,
        previous_result=_result(second_screen),
    )

    assert first.action is not None and first.action.action_type == "NOOP"
    assert second.action is not None and second.action.action_type == "CLICK"
    assert len(transport.model_requests) == 2
    state = host.get(EPISODE_ID)
    assert state.resume_phase is SessionResumePhase.INTENT_ISSUED
    assert state.step_index == 1
    assert state.model_attempts == 2
    assert len(host.session_store.records(EPISODE_ID)) == 2
    records = host.session_store.records(EPISODE_ID)
    result_event = host.journal.event(f"{EPISODE_ID}/step-0000/result_reported")
    assert result_event is not None
    assert records[0].checkpoints.post_dispatch == result_event.payload[
        "post_dispatch_checkpoint_digest"
    ]
    assert records[1].checkpoints.post_dispatch is None
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "result_reported"
    ) == 1


def test_close_applies_the_outstanding_result_and_is_an_exactly_once_replay(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    intent = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    final_screen = b"screen-1"
    final_result = _result(final_screen, terminated=True)
    arguments = {
        "episode_id": EPISODE_ID,
        "final_intent_id": intent.intent_id,
        "final_result": final_result,
        "final_screenshot_sha256": final_result.screenshot_sha256,
        "final_screenshot_object_key": "serving-final-screenshots/final.png",
    }

    closed = host.close_episode(**arguments)
    replay = host.close_episode(**arguments)

    assert replay == closed
    assert closed.terminal_classification.value == "terminated"
    assert closed.steps == 1
    assert host.get(EPISODE_ID).resume_phase is SessionResumePhase.CLOSED
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_closed"
    ) == 1
    with pytest.raises(EpisodeEndedError):
        host.act(episode_id=EPISODE_ID, screenshot=final_screen)


def test_close_rejects_a_mismatched_final_screenshot_before_state_changes(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    intent = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    before = host.get(EPISODE_ID)

    with pytest.raises(IntentReferenceError, match="must match"):
        host.close_episode(
            episode_id=EPISODE_ID,
            final_intent_id=intent.intent_id,
            final_result=_result(b"screen-1"),
            final_screenshot_sha256="sha256:" + sha256_bytes(b"other"),
            final_screenshot_object_key="serving-final-screenshots/final.png",
        )

    assert host.get(EPISODE_ID) == before
    assert host.journal.event(f"{EPISODE_ID}/episode_closed") is None


def test_intent_reference_and_screenshot_digest_are_validated_before_mutation(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete the form", client_episode_ref="client-1")
    first = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    before_events = host.journal.events(EPISODE_ID)
    before_revision = host.get(EPISODE_ID).revision

    with pytest.raises(IntentReferenceError, match="outstanding intent"):
        host.act(
            episode_id=EPISODE_ID,
            screenshot=b"screen-1",
            previous_intent_id="intent-" + "9" * 32,
            previous_result=_result(b"screen-1"),
        )
    with pytest.raises(IntentReferenceError, match="must match"):
        host.act(
            episode_id=EPISODE_ID,
            screenshot=b"screen-1",
            previous_intent_id=first.intent_id,
            previous_result=_result(b"different-screen"),
        )

    assert host.journal.events(EPISODE_ID) == before_events
    assert host.get(EPISODE_ID).revision == before_revision


def test_step_record_allows_only_exact_post_dispatch_completion(tmp_path: Path) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    state = host.get(EPISODE_ID)
    record = host.session_store.records(EPISODE_ID)[0]
    tampered = replace(
        record,
        screenshot_sha256="sha256:" + "0" * 64,
        checkpoints=replace(
            record.checkpoints,
            post_dispatch=record.checkpoints.post_parse,
        ),
    )

    with pytest.raises(SessionConflictError, match="changed after commit"):
        host.session_store.save(
            replace(state, revision=state.revision + 1),
            expected_revision=state.revision,
            step_record=tampered,
        )

    assert host.get(EPISODE_ID) == state
    assert host.session_store.records(EPISODE_ID) == (record,)


@pytest.mark.parametrize(
    "boundary",
    [
        "pre_call",
        "canonical_response_persisted",
        "attempt_terminal",
        "post_attempt",
        "parsed_action_candidate",
        "post_parse",
        "intent_issued",
        "step_recorded",
    ],
)
def test_restart_recovers_each_replayable_durable_boundary_without_duplicate_call(
    tmp_path: Path, boundary: str
) -> None:
    baseline = _host(tmp_path / "baseline")
    baseline.create_episode(
        task_instruction="Complete the form", client_episode_ref="client-1"
    )
    baseline.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    expected_checkpoint = baseline.get(EPISODE_ID).policy_checkpoint_sha256

    transport = ScriptedTransport()
    interrupted = _host(
        tmp_path / "recovered", transport=transport, interrupt_after=boundary
    )
    interrupted.create_episode(
        task_instruction="Complete the form", client_episode_ref="client-1"
    )
    with pytest.raises(InjectedInterruption, match=boundary):
        interrupted.act(episode_id=EPISODE_ID, screenshot=b"screen-0")

    recovered = _host(tmp_path / "recovered", transport=transport)
    result = recovered.act(episode_id=EPISODE_ID, screenshot=b"screen-0")

    assert result.sealed_failure is None
    assert result.intent_id is not None
    assert len(transport.model_requests) == 1
    assert len(recovered.session_store.records(EPISODE_ID)) == 1
    assert recovered.get(EPISODE_ID).policy_checkpoint_sha256 == expected_checkpoint
    checkpoints = recovered.session_store.records(EPISODE_ID)[0].checkpoints
    assert checkpoints.pre_call == recovered.journal.event(
        f"{EPISODE_ID}/step-0000/attempt-00/attempt_started"
    ).payload["pre_call_checkpoint_digest"]


def test_restart_after_initialized_boundary_uses_durable_reset_checkpoint(
    tmp_path: Path,
) -> None:
    interrupted = _host(tmp_path, interrupt_after="initialized")
    with pytest.raises(InjectedInterruption, match="initialized"):
        interrupted.create_episode(
            task_instruction="Complete the form", client_episode_ref="client-1"
        )

    transport = ScriptedTransport()
    recovered = _host(tmp_path, transport=transport)
    result = recovered.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    assert result.intent_id is not None
    assert len(transport.model_requests) == 1


def test_restart_rejects_a_changed_in_progress_act_without_sealing(tmp_path: Path) -> None:
    interrupted = _host(tmp_path, interrupt_after="pre_call")
    interrupted.create_episode(
        task_instruction="Complete the form", client_episode_ref="client-1"
    )
    with pytest.raises(InjectedInterruption, match="pre_call"):
        interrupted.act(episode_id=EPISODE_ID, screenshot=b"screen-0")

    recovered = _host(tmp_path)
    before = recovered.get(EPISODE_ID)
    with pytest.raises(IntentReferenceError, match="durable request context"):
        recovered.act(episode_id=EPISODE_ID, screenshot=b"changed-screen")
    assert recovered.get(EPISODE_ID) == before
    assert recovered.journal.event(f"{EPISODE_ID}/episode_sealed") is None


@pytest.mark.parametrize(
    ("boundary", "requests_before_restart"),
    [("attempt_started", 0), ("provider_receipt", 1)],
)
def test_restart_with_unknown_reserved_attempt_seals_infrastructure_failure_once(
    tmp_path: Path, boundary: str, requests_before_restart: int
) -> None:
    transport = ScriptedTransport()
    interrupted = _host(tmp_path, transport=transport, interrupt_after=boundary)
    interrupted.create_episode(
        task_instruction="Complete the form", client_episode_ref="client-1"
    )
    with pytest.raises(InjectedInterruption, match=boundary):
        interrupted.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    assert len(transport.model_requests) == requests_before_restart

    recovered = _host(tmp_path, transport=transport)
    result = recovered.act(episode_id=EPISODE_ID, screenshot=b"screen-0")

    assert result.sealed_failure is SealedFailure.INFRASTRUCTURE_FAILURE
    assert len(transport.model_requests) == requests_before_restart
    assert [event.kind for event in recovered.journal.events(EPISODE_ID)].count(
        "episode_sealed"
    ) == 1
    assert len(recovered.session_store.records(EPISODE_ID)) == 1
    with pytest.raises(EpisodeEndedError):
        recovered.act(episode_id=EPISODE_ID, screenshot=b"screen-0")


@pytest.mark.parametrize("boundary", ["result_reported", "post_dispatch"])
def test_result_reporting_boundaries_recover_without_reapplying_policy_result(
    tmp_path: Path, boundary: str
) -> None:
    transport = ScriptedTransport()
    initial = _host(tmp_path, transport=transport)
    initial.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    first = initial.act(episode_id=EPISODE_ID, screenshot=b"screen-0")

    interrupted = _host(tmp_path, transport=transport, interrupt_after=boundary)
    screen = b"screen-1"
    with pytest.raises(InjectedInterruption, match=boundary):
        interrupted.act(
            episode_id=EPISODE_ID,
            screenshot=screen,
            previous_intent_id=first.intent_id,
            previous_result=_result(screen),
        )

    recovered = _host(tmp_path, transport=transport)
    second = recovered.act(
        episode_id=EPISODE_ID,
        screenshot=screen,
        previous_intent_id=first.intent_id,
        previous_result=_result(screen),
    )
    assert second.action is not None and second.action.action_type == "CLICK"
    assert len(transport.model_requests) == 2
    assert [event.kind for event in recovered.journal.events(EPISODE_ID)].count(
        "result_reported"
    ) == 1
    result_event = recovered.journal.event(f"{EPISODE_ID}/step-0000/result_reported")
    assert result_event is not None
    assert recovered.session_store.records(EPISODE_ID)[0].checkpoints.post_dispatch == (
        result_event.payload["post_dispatch_checkpoint_digest"]
    )


@pytest.mark.parametrize(
    ("outcomes", "action", "expected"),
    [
        (
            [
                TransportOutcome(
                    "response",
                    {
                        "response_id": "parse-bad",
                        "model": "fake",
                        "content": "not-json",
                        "finish_reason": "stop",
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            ],
            NOOP,
            SealedFailure.PARSE_FAILURE,
        ),
        ([], {"action_type": 1, "x": 1024, "y": 10, "key": 0}, SealedFailure.INVALID_ACTION),
        ([TransportOutcome("pre_send_failure", failure_code="local-failure")], NOOP, SealedFailure.REQUEST_FAILURE),
    ],
)
def test_each_policy_failure_is_sealed_once_and_never_attempted_again(
    tmp_path: Path,
    outcomes: list[TransportOutcome],
    action: dict[str, int],
    expected: SealedFailure,
) -> None:
    transport = ScriptedTransport(outcomes)
    host = _host(tmp_path, actions=(action,), transport=transport)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")

    result = host.act(episode_id=EPISODE_ID, screenshot=b"screen")
    assert result.sealed_failure is expected
    assert len(host.session_store.records(EPISODE_ID)) == 1
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_sealed"
    ) == 1
    requests = len(transport.model_requests)
    with pytest.raises(EpisodeEndedError):
        host.act(episode_id=EPISODE_ID, screenshot=b"screen")
    assert len(transport.model_requests) == requests


def test_sealed_episode_can_close_once_with_valid_closed_state(tmp_path: Path) -> None:
    host = _host(tmp_path, actions=({"action_type": 1, "x": 1024, "y": 10, "key": 0},))
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    result = host.act(episode_id=EPISODE_ID, screenshot=b"screen")
    assert result.sealed_failure is SealedFailure.INVALID_ACTION

    closed = host.close_episode(
        episode_id=EPISODE_ID,
        final_intent_id=None,
        final_result=None,
        final_screenshot_sha256=None,
        final_screenshot_object_key=None,
    )
    replay = host.close_episode(
        episode_id=EPISODE_ID,
        final_intent_id=None,
        final_result=None,
        final_screenshot_sha256=None,
        final_screenshot_object_key=None,
    )

    state = host.get(EPISODE_ID)
    assert replay == closed
    assert closed.terminal_classification.value == "invalid_action"
    assert state.resume_phase is SessionResumePhase.CLOSED
    assert state.sealed_failure is None
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_closed"
    ) == 1


def test_restart_after_sealed_event_rejects_changed_terminal_record_input(
    tmp_path: Path,
) -> None:
    response = {
        "response_id": "parse-bad",
        "model": "fake",
        "content": "not-json",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    transport = ScriptedTransport([TransportOutcome("response", response)])
    interrupted = _host(
        tmp_path, transport=transport, interrupt_after="sealed_event"
    )
    interrupted.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    with pytest.raises(InjectedInterruption, match="sealed_event"):
        interrupted.act(episode_id=EPISODE_ID, screenshot=b"original-screen")

    recovered = _host(tmp_path, transport=transport)
    before = recovered.get(EPISODE_ID)
    with pytest.raises(IntentReferenceError, match="durable request context"):
        recovered.act(episode_id=EPISODE_ID, screenshot=b"changed-screen")
    assert recovered.get(EPISODE_ID) == before
    assert recovered.session_store.records(EPISODE_ID) == ()

    result = recovered.act(episode_id=EPISODE_ID, screenshot=b"original-screen")
    assert result.sealed_failure is SealedFailure.PARSE_FAILURE
    assert len(transport.model_requests) == 1
    assert len(recovered.session_store.records(EPISODE_ID)) == 1


def test_deployment_call_cap_is_checked_before_send_and_seals_open_episode(
    tmp_path: Path,
) -> None:
    transport = ScriptedTransport()
    host = _host(tmp_path, transport=transport, attempt_cap=1)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    first = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    screen = b"screen-1"
    capped = host.act(
        episode_id=EPISODE_ID,
        screenshot=screen,
        previous_intent_id=first.intent_id,
        previous_result=_result(screen),
    )
    assert capped.sealed_failure is SealedFailure.CAP_REACHED
    assert len(transport.model_requests) == 1
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_sealed"
    ) == 1
    with pytest.raises(EpisodeEndedError):
        host.act(episode_id=EPISODE_ID, screenshot=screen)
    assert len(transport.model_requests) == 1
    with pytest.raises(ServingEpisodeError, match="refusing a new episode"):
        host.create_episode(task_instruction="Complete", client_episode_ref="client-2")


def test_unrelated_runtime_error_with_cap_wording_seals_as_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")

    def fail_reservation(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("unrelated fault whose message says cap reached")

    monkeypatch.setattr(host.journal, "reserve_attempt_started", fail_reservation)
    result = host.act(episode_id=EPISODE_ID, screenshot=b"screen")

    assert result.sealed_failure is SealedFailure.INFRASTRUCTURE_FAILURE


def test_per_action_attempt_cap_seals_the_second_retryable_response(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    host = _host(
        tmp_path,
        transport=transport,
        policy=AlwaysRetryPolicy((NOOP,)),
    )
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")

    result = host.act(episode_id=EPISODE_ID, screenshot=b"screen")

    assert result.sealed_failure is SealedFailure.INFRASTRUCTURE_FAILURE
    assert result.attempt_count == 2
    assert len(transport.model_requests) == 2
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_sealed"
    ) == 1


def test_max_steps_seals_before_an_extra_provider_call(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    host = _host(tmp_path, transport=transport, max_steps=1)
    host.create_episode(task_instruction="Complete", client_episode_ref="client-1")
    first = host.act(episode_id=EPISODE_ID, screenshot=b"screen-0")
    screen = b"screen-1"
    result = host.act(
        episode_id=EPISODE_ID,
        screenshot=screen,
        previous_intent_id=first.intent_id,
        previous_result=_result(screen),
    )
    assert result.sealed_failure is SealedFailure.MAX_STEPS_REACHED
    assert len(transport.model_requests) == 1
    assert [event.kind for event in host.journal.events(EPISODE_ID)].count(
        "episode_sealed"
    ) == 1
    with pytest.raises(EpisodeEndedError):
        host.act(episode_id=EPISODE_ID, screenshot=screen)
    assert len(transport.model_requests) == 1
