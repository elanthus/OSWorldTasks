"""Representative, schema-valid stateful serving documents shared by platform tests."""

from __future__ import annotations

from typing import Any

from pixelgym.grounding.v5.contracts import (
    PolicyManifest,
    SandboxManifest,
    sandbox_endpoint_allowlist_digest,
)
from pixelgym.platform.stateful_contracts import (
    EpisodeClosedRecord,
    EpisodeOpenedRecord,
    EpisodeSessionState,
    EpisodeStepRecord,
    EvidenceBinding,
    EvidenceClass,
    ReportedResult,
    ServedAction,
    ServingIdentity,
    SessionResumePhase,
    StatefulPolicyPackage,
    StepCheckpoints,
)

D = "sha256:" + "a" * 64
EPISODE = "ep-" + "0" * 32
INTENT = "intent-" + "1" * 32
ENDPOINT = "http://127.0.0.1:9"


def v5_manifest() -> PolicyManifest:
    sandbox = SandboxManifest(
        runtime_digest="sha256:" + "b" * 64,
        network_policy_version="v5-net-1",
        provider_endpoint=ENDPOINT,
        endpoint_allowlist_digest=sandbox_endpoint_allowlist_digest(
            ENDPOINT, policy_version="v5-net-1"
        ),
        denied_capabilities=tuple(sorted(SandboxManifest.REQUIRED_DENIALS)),
    )
    return PolicyManifest.build(
        provider="fake-provider",
        model="fake/model-1",
        exact_snapshot=True,
        harness_digest="sha256:" + "c" * 64,
        dependency_lock_digest="sha256:" + "d" * 64,
        system_prompt_digest="sha256:" + "e" * 64,
        task_renderer_version="v5-renderer-1",
        response_schema_version="v5-response-1",
        state_reducer_version="v5-reducer-1",
        parser_version="v5-parser-1",
        memory_policy_version="v5-memory-1",
        coordinate_adapter="native",
        coordinate_adapter_digest="sha256:" + "f" * 64,
        coordinate_input_convention="integer-pixel/1024x768",
        max_model_attempts_per_action=2,
        max_cancellation_requests_per_attempt=0,
        max_reconciliation_requests_per_attempt=0,
        request_deadline_seconds=30.0,
        cancellation_mode="none",
        reconciliation_deadline_seconds=0.0,
        sandbox=sandbox,
        code_revision="1" * 40,
        dirty_worktree_policy="reject",
    )


def evidence(kind: EvidenceClass = EvidenceClass.CALIBRATION) -> EvidenceBinding:
    return EvidenceBinding(
        run_kind=kind.run_kind,
        run_reference="artifacts/grounding-v5-d56-gemini-v3b-full-calibration-run",
        plan_sha256=D,
        summary_sha256=D,
        assigned_episodes=50,
        attempted_episodes=50,
        exact_successes=35,
    )


def package(kind: EvidenceClass = EvidenceClass.CALIBRATION) -> StatefulPolicyPackage:
    return StatefulPolicyPackage.build(
        manifest=v5_manifest(),
        model_alias_disclosure=None,
        package_source_sha256="9" * 64,
        dependency_lock_sha256="8" * 64,
        max_steps=40,
        evidence_class=kind,
        evidence=evidence(kind),
        code_revision="2" * 40,
        code_state="clean",
        source_tree_sha256="7" * 64,
        source_provenance_verified=True,
        source_provenance_failure_reason=None,
    )


def identity() -> ServingIdentity:
    return ServingIdentity(
        policy_id=package().policy_id,
        deployment_id="deploy-1",
        exact_policy_version="1",
        evidence_class=EvidenceClass.CALIBRATION,
    )


def result() -> ReportedResult:
    return ReportedResult(reward=0.0, terminated=False, truncated=False, screenshot_sha256=D)


def session_state(
    *,
    phase: SessionResumePhase = SessionResumePhase.INTENT_ISSUED,
) -> EpisodeSessionState:
    sealed = phase is SessionResumePhase.SEALED
    has_intent = phase not in {SessionResumePhase.INITIALIZED, SessionResumePhase.SEALED}
    return EpisodeSessionState(
        episode_id=EPISODE,
        client_episode_ref="client-ep-1",
        identity=identity(),
        created_at_utc="2026-09-09T00:00:00+00:00",
        updated_at_utc="2026-09-09T00:00:10+00:00",
        revision=3,
        resume_phase=phase,
        task_instruction_sha256=D,
        screen={"width": 1024, "height": 768},
        max_steps=40,
        max_model_attempts_per_action=2,
        deployment_attempt_cap=500,
        step_index=1,
        policy_checkpoint_sha256=D,
        policy_checkpoint_object_key="serving-policy-checkpoints/" + "a" * 64 + ".json",
        last_intent_id=INTENT if has_intent else None,
        last_intent_status=(
            "issued"
            if phase is SessionResumePhase.INTENT_ISSUED
            else "result_reported" if has_intent else "sealed" if sealed else "none"
        ),
        last_action=ServedAction("CLICK", x=10, y=20) if has_intent else None,
        sealed_failure="parse_failure" if sealed else None,
        terminal_classification=(
            "parse_failure"
            if sealed
            else "terminated" if phase is SessionResumePhase.CLOSED else None
        ),
        model_attempts=2,
        provider_control_requests=0,
        usage={"prompt_tokens": 200},
        attributed_cost_usd=0.0002,
    )


def step_record(*, sealed: bool = False) -> EpisodeStepRecord:
    return EpisodeStepRecord(
        episode_id=EPISODE,
        step_index=1,
        identity=identity(),
        recorded_at_utc="2026-09-09T00:00:00+00:00",
        screenshot_sha256=D,
        previous_intent_id=INTENT,
        previous_result=result(),
        attempt_ids=("trial/1/step/1/attempt/0",),
        canonical_response_sha256s=(D,),
        checkpoints=StepCheckpoints(
            pre_call=D, post_attempt=D, post_parse=None if sealed else D, post_dispatch=None
        ),
        intent_id=None if sealed else INTENT,
        action=None if sealed else ServedAction("CLICK", x=10, y=20),
        sealed_failure="parse_failure" if sealed else None,
        provider_request_ids=("gen-1",),
        latency_ms=12.5,
        usage={"prompt_tokens": 100, "completion_tokens": 5},
        attributed_cost_usd=0.0001,
    )


def stateful_representatives() -> dict[str, dict[str, Any]]:
    ident = identity().to_dict()
    return {
        "stateful_policy_package": package().to_dict(),
        "serving_create_request": {
            "schema_version": "pixelgym-serving-session-v2",
            "task_instruction": "Fill in the vendor form.",
            "screen_width": 1024,
            "screen_height": 768,
            "client_episode_ref": "client-ep-1",
        },
        "serving_create_response": {
            "schema_version": "pixelgym-serving-session-v2",
            "episode_id": EPISODE,
            "identity": ident,
            "max_steps": 40,
            "max_model_attempts_per_action": 2,
            "action_schema_version": "pixelgym-action-v1",
            "key_allowlist_version": 1,
        },
        "serving_act_request": {
            "schema_version": "pixelgym-serving-session-v2",
            "screenshot": {"image_base64": "aGk=", "media_type": "image/png"},
            "previous_intent_id": INTENT,
            "previous_result": result().to_dict(),
        },
        "serving_act_response": {
            "schema_version": "pixelgym-serving-session-v2",
            "episode_id": EPISODE,
            "step_index": 1,
            "intent_id": INTENT,
            "action": ServedAction("KEY", key=3).to_dict(),
            "sealed_failure": None,
            "attempt_count": 1,
            "identity": ident,
        },
        "serving_close_request": {
            "schema_version": "pixelgym-serving-session-v2",
            "final_screenshot": {"image_base64": "aGk=", "media_type": "image/png"},
            "final_intent_id": INTENT,
            "final_result": result().to_dict(),
        },
        "serving_close_response": {
            "schema_version": "pixelgym-serving-session-v2",
            "episode_id": EPISODE,
            "identity": ident,
            "terminal_classification": "terminated",
            "steps": 3,
            "model_attempts": 3,
            "provider_control_requests": 0,
            "usage": {"prompt_tokens": 300},
            "attributed_cost_usd": 0.0003,
            "final_screenshot_sha256": D,
        },
        "serving_episode_status": {
            "schema_version": "pixelgym-serving-session-v2",
            "episode_id": EPISODE,
            "identity": ident,
            "open": True,
            "step_index": 1,
            "last_intent_id": INTENT,
            "last_intent_status": "issued",
        },
        "episode_session_state": session_state().to_dict(),
        "episode_opened_record": EpisodeOpenedRecord(
            episode_id=EPISODE,
            client_episode_ref="client-ep-1",
            identity=identity(),
            opened_at_utc="2026-09-09T00:00:00+00:00",
            task_instruction_sha256=D,
            screen={"width": 1024, "height": 768},
            max_steps=40,
            deployment_attempt_cap=500,
        ).to_dict(),
        "episode_step_record": step_record().to_dict(),
        "episode_closed_record": EpisodeClosedRecord(
            episode_id=EPISODE,
            identity=identity(),
            closed_at_utc="2026-09-09T00:01:00+00:00",
            terminal_classification="terminated",
            final_intent_id=INTENT,
            final_result=ReportedResult(1.0, True, False, D),
            final_screenshot_sha256=D,
            final_screenshot_object_key="serving-final-screenshots/" + "a" * 64 + ".png",
            steps=3,
            model_attempts=3,
            provider_control_requests=0,
            usage={"prompt_tokens": 300},
            attributed_cost_usd=0.0003,
        ).to_dict(),
    }
