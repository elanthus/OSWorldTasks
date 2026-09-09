"""S2 of plans/v5-policy-serving.md: frozen stateful package, session, and record contracts."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from pixelgym.platform.approved_providers import (
    ApprovedProviderError,
    ApprovedProviderPolicy,
    resolve_approved_provider,
)
from pixelgym.platform.schema_validation import ContractValidationError, PlatformSchemas
from pixelgym.platform.stateful_contracts import (
    EpisodeClosedRecord,
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
from tests.unit.platform.stateful_fixtures import (
    EPISODE,
    INTENT,
    D,
    evidence,
    identity,
    package,
    result,
    session_state,
    stateful_representatives,
    step_record,
    v5_manifest,
)
from tests.unit.platform.test_approved_providers import RECORD


@pytest.fixture
def schemas(repository_root: Path) -> PlatformSchemas:
    return PlatformSchemas(repository_root)


# --- package identity -----------------------------------------------------------------------


def test_package_binds_the_v5_manifest_by_digest_and_derives_a_stable_policy_id() -> None:
    first, second = package(), package()
    assert first.policy_id == second.policy_id
    assert first.policy_id.startswith("sha256:")
    assert first.v5_policy_id == v5_manifest().policy_id
    assert first.policy_manifest["policy_id"] == first.v5_policy_id
    assert first.screen == {"width": 1024, "height": 768}
    assert first.action_schema_version == "pixelgym-action-v1"
    assert first.key_allowlist_version == 1


def test_package_policy_id_changes_with_every_identity_field_but_not_manifest_rendering() -> None:
    base = package()
    confirmatory = package(EvidenceClass.CONFIRMATORY)
    assert confirmatory.policy_id != base.policy_id
    assert confirmatory.evidence.run_kind == "d59-confirmatory"

    rendered = base.to_dict()
    rendered["policy_manifest"] = dict(reversed(list(rendered["policy_manifest"].items())))
    rendered["evidence"] = EvidenceBinding(**rendered["evidence"])
    rebuilt = StatefulPolicyPackage(**rendered)
    assert rebuilt.policy_id == base.policy_id

    tampered = base.to_dict()
    tampered["max_steps"] = 41
    tampered["evidence"] = EvidenceBinding(**tampered["evidence"])
    with pytest.raises(ValueError, match="policy_id does not match"):
        StatefulPolicyPackage(**tampered)


def test_package_rejects_mismatched_evidence_class_and_bad_counts() -> None:
    with pytest.raises(ValueError, match="run_kind does not match"):
        StatefulPolicyPackage.build(
            manifest=v5_manifest(),
            model_alias_disclosure=None,
            package_source_sha256="9" * 64,
            dependency_lock_sha256="8" * 64,
            max_steps=40,
            evidence_class=EvidenceClass.CONFIRMATORY,
            evidence=evidence(EvidenceClass.CALIBRATION),
            code_revision="2" * 40,
            code_state="clean",
            source_tree_sha256="7" * 64,
            source_provenance_verified=True,
            source_provenance_failure_reason=None,
        )
    with pytest.raises(ValueError, match="successes <= attempted <= assigned"):
        EvidenceBinding("d56-calibration", "run", D, D, 50, 50, 51)
    with pytest.raises(ValueError, match="run_kind"):
        EvidenceBinding("d57-something", "run", D, D, 50, 50, 1)


@pytest.mark.parametrize("code_state", ["clean", "dirty"])
def test_package_accepts_only_verified_provenance_for_verifiable_code(code_state: str) -> None:
    base = {
        "manifest": v5_manifest(),
        "model_alias_disclosure": None,
        "package_source_sha256": "9" * 64,
        "dependency_lock_sha256": "8" * 64,
        "max_steps": 40,
        "evidence_class": EvidenceClass.CALIBRATION,
        "evidence": evidence(),
        "code_revision": "2" * 40,
        "code_state": code_state,
        "source_tree_sha256": "7" * 64,
        "source_provenance_verified": True,
        "source_provenance_failure_reason": None,
    }
    assert StatefulPolicyPackage.build(**base).code_state == code_state
    invalid = (
        {"code_revision": "not-a-git-sha"},
        {"source_tree_sha256": None},
        {"source_tree_sha256": "not-a-digest"},
        {"source_provenance_verified": False},
        {"source_provenance_failure_reason": "unexpected"},
    )
    for changes in invalid:
        with pytest.raises(ValueError):
            StatefulPolicyPackage.build(**{**base, **changes})


def test_package_accepts_only_failed_provenance_for_unverifiable_code() -> None:
    base = {
        "manifest": v5_manifest(),
        "model_alias_disclosure": None,
        "package_source_sha256": "9" * 64,
        "dependency_lock_sha256": "8" * 64,
        "max_steps": 40,
        "evidence_class": EvidenceClass.CALIBRATION,
        "evidence": evidence(),
        "code_revision": "unverifiable",
        "code_state": "unverifiable",
        "source_tree_sha256": None,
        "source_provenance_verified": False,
        "source_provenance_failure_reason": "source archive omitted Git metadata",
    }
    assert StatefulPolicyPackage.build(**base).code_state == "unverifiable"
    invalid = (
        {"code_revision": "2" * 40},
        {"source_tree_sha256": "7" * 64},
        {"source_provenance_verified": True},
        {"source_provenance_failure_reason": None},
        {"source_provenance_failure_reason": ""},
    )
    for changes in invalid:
        with pytest.raises(ValueError):
            StatefulPolicyPackage.build(**{**base, **changes})


def test_package_rejects_an_embedded_manifest_that_disagrees_with_its_bindings() -> None:
    fields = package().to_dict()
    fields["evidence"] = EvidenceBinding(**fields["evidence"])

    tampered = copy.deepcopy(fields)
    tampered["policy_manifest"]["model"] = "fake/replaced-model"
    with pytest.raises(ValueError, match="frozen v5 contract|embedded manifest"):
        StatefulPolicyPackage(**tampered)

    with pytest.raises(ValueError, match="provider and model"):
        StatefulPolicyPackage(**{**fields, "model": "fake/replaced-model"})
    with pytest.raises(ValueError, match="sandbox_manifest_sha256"):
        StatefulPolicyPackage(**{**fields, "sandbox_manifest_sha256": "0" * 64})


# --- wire types -----------------------------------------------------------------------------


def test_served_action_operands_are_exact_per_type() -> None:
    assert ServedAction("NOOP").to_dict() == {
        "action_type": "NOOP",
        "x": None,
        "y": None,
        "key": None,
    }
    assert ServedAction("CLICK", x=0, y=767).to_dict()["y"] == 767
    assert ServedAction("KEY", key=0).to_dict()["key"] == 0
    for bad in (
        {"action_type": "NOOP", "x": 0},
        {"action_type": "CLICK", "x": 1},
        {"action_type": "CLICK", "x": 1, "y": -1},
        {"action_type": "CLICK", "x": True, "y": 1},
        {"action_type": "KEY", "key": 1, "x": 1},
        {"action_type": "DONE"},
    ):
        with pytest.raises(ValueError):
            ServedAction(**bad)


def test_reported_result_is_the_exact_environment_signal() -> None:
    assert result().reward == 0.0
    with pytest.raises(ValueError, match="0.0 or 1.0"):
        ReportedResult(0.5, False, False, D)
    with pytest.raises(ValueError, match="0.0 or 1.0"):
        ReportedResult(1, False, False, D)
    with pytest.raises(ValueError, match="never both"):
        ReportedResult(1.0, True, True, D)
    with pytest.raises(ValueError, match="canonical sha256"):
        ReportedResult(0.0, False, False, "abc")


def test_identity_requires_an_evidence_class() -> None:
    with pytest.raises(ValueError):
        ServingIdentity(package().policy_id, "d", "1", "benchmark")
    assert identity().to_dict()["evidence_class"] == "calibration"


# --- records --------------------------------------------------------------------------------


def test_step_record_yields_exactly_one_of_intent_or_sealed_failure() -> None:
    assert step_record().sealed_failure is None
    assert step_record(sealed=True).intent_id is None
    base = step_record().to_dict()
    base["identity"] = identity()
    base["previous_result"] = result()
    base["checkpoints"] = StepCheckpoints(D, D, D, None)
    base["action"] = ServedAction("NOOP")
    base["attempt_ids"] = tuple(base["attempt_ids"])
    base["canonical_response_sha256s"] = tuple(base["canonical_response_sha256s"])
    base["provider_request_ids"] = tuple(base["provider_request_ids"])
    for key in ("schema_version", "record_kind"):
        base.pop(key)
    with pytest.raises(ValueError, match="exactly one"):
        EpisodeStepRecord(**{**base, "sealed_failure": "cap_reached"})
    with pytest.raises(ValueError, match="exactly one"):
        EpisodeStepRecord(**{**base, "intent_id": None, "action": None})
    with pytest.raises(ValueError, match="first step cannot"):
        EpisodeStepRecord(**{**base, "step_index": 0})
    with pytest.raises(ValueError, match="must report the previous intent"):
        EpisodeStepRecord(**{**base, "previous_intent_id": None, "previous_result": None})
    with pytest.raises(ValueError, match="more canonical responses"):
        EpisodeStepRecord(**{**base, "attempt_ids": ()})


def test_checkpoints_must_be_sequential() -> None:
    with pytest.raises(ValueError, match="post_parse requires"):
        StepCheckpoints(D, None, D, None)
    with pytest.raises(ValueError, match="post_dispatch requires"):
        StepCheckpoints(D, D, None, D)


def test_session_store_freezes_every_recoverable_and_terminal_phase() -> None:
    schemas = PlatformSchemas(Path(__file__).resolve().parents[3])
    for phase in SessionResumePhase:
        state = session_state(phase=phase)
        schemas.validate("episode_session_state", state.to_dict())


def test_session_store_requires_a_durable_checkpoint_and_consistent_intent_state() -> None:
    state = session_state()
    with pytest.raises(ValueError, match="policy_checkpoint_object_key"):
        replace(state, policy_checkpoint_object_key="")
    with pytest.raises(ValueError, match="last_intent_id and last_action"):
        replace(state, last_action=None)
    with pytest.raises(ValueError, match="intent_issued phase"):
        replace(state, resume_phase=SessionResumePhase.POST_PARSE)
    with pytest.raises(ValueError, match="terminal classification"):
        replace(
            session_state(phase=SessionResumePhase.CLOSED),
            terminal_classification=None,
        )
    with pytest.raises(ValueError, match="must match"):
        replace(
            session_state(phase=SessionResumePhase.SEALED),
            terminal_classification="request_failure",
        )


def test_closed_record_requires_the_stored_final_screenshot_with_a_final_result() -> None:
    fields = {
        "episode_id": EPISODE,
        "identity": identity(),
        "closed_at_utc": "2026-09-09T00:01:00+00:00",
        "terminal_classification": "closed_by_caller",
        "final_intent_id": None,
        "final_result": None,
        "final_screenshot_sha256": None,
        "final_screenshot_object_key": None,
        "steps": 0,
        "model_attempts": 0,
        "provider_control_requests": 0,
        "usage": None,
        "attributed_cost_usd": None,
    }
    assert EpisodeClosedRecord(**fields).to_dict()["final_screenshot_sha256"] is None
    with pytest.raises(ValueError, match="requires the stored final screenshot"):
        EpisodeClosedRecord(**{**fields, "final_intent_id": INTENT, "final_result": result()})
    with pytest.raises(ValueError, match="both a digest and an object key"):
        EpisodeClosedRecord(**{**fields, "final_screenshot_sha256": D})


# --- JSON Schema agreement ------------------------------------------------------------------


def test_every_representative_validates_and_round_trips_through_its_schema(
    schemas: PlatformSchemas,
) -> None:
    for contract, value in stateful_representatives().items():
        schemas.validate(contract, value)


@pytest.mark.parametrize(
    ("contract", "mutate", "fragment"),
    [
        (
            "stateful_policy_package",
            lambda v: v.update(evidence_class="benchmark"),
            "stateful_policy_package",
        ),
        ("stateful_policy_package", lambda v: v.update(kind="grounding"), "kind"),
        (
            "stateful_policy_package",
            lambda v: v["evidence"].update(run_kind="d59-confirmatory"),
            "run_kind",
        ),
        (
            "serving_act_request",
            lambda v: v["previous_result"].update(terminated=True, truncated=True),
            "previous_result",
        ),
        ("serving_create_request", lambda v: v.update(task_instruction=""), "task_instruction"),
        (
            "serving_create_request",
            lambda v: v.update(client_episode_ref="has space"),
            "client_episode_ref",
        ),
        ("serving_act_request", lambda v: v.update(previous_result=None), "serving_act_request"),
        (
            "serving_act_request",
            lambda v: v["screenshot"].update(media_type="image/gif"),
            "media_type",
        ),
        (
            "serving_act_response",
            lambda v: v.update(sealed_failure="parse_failure"),
            "serving_act_response",
        ),
        ("serving_act_response", lambda v: v["action"].update(x=1), "serving_act_response"),
        (
            "serving_close_request",
            lambda v: v.update(final_screenshot=None),
            "serving_close_request",
        ),
        (
            "serving_close_response",
            lambda v: v.update(terminal_classification="success"),
            "terminal_classification",
        ),
        ("episode_step_record", lambda v: v.update(raw_response="{}"), "raw_response"),
        ("episode_step_record", lambda v: v.update(policy_state="..."), "policy_state"),
        ("episode_step_record", lambda v: v["previous_result"].update(reward=0.5), "reward"),
        (
            "episode_closed_record",
            lambda v: v.update(final_screenshot_object_key=None),
            "episode_closed_record",
        ),
        (
            "episode_opened_record",
            lambda v: v.update(task_instruction="Fill in the form"),
            "task_instruction",
        ),
        (
            "episode_session_state",
            lambda v: v.update(policy_checkpoint_object_key=""),
            "policy_checkpoint_object_key",
        ),
        (
            "episode_session_state",
            lambda v: v.update(last_intent_status="result_reported"),
            "episode_session_state",
        ),
        (
            "episode_session_state",
            lambda v: v.update(
                resume_phase="sealed",
                sealed_failure="parse_failure",
                terminal_classification="request_failure",
                last_intent_status="sealed",
            ),
            "episode_session_state",
        ),
    ],
)
def test_schemas_reject_leaks_and_boundary_violations(
    schemas: PlatformSchemas, contract: str, mutate, fragment: str
) -> None:
    value = copy.deepcopy(stateful_representatives()[contract])
    mutate(value)
    with pytest.raises(ContractValidationError, match=fragment):
        schemas.validate(contract, value)


def test_v5_manifest_embedded_in_the_package_validates_against_the_v5_schema() -> None:
    import json
    from importlib import resources

    from jsonschema import Draft202012Validator

    schema = json.loads(
        resources.files("pixelgym.grounding.v5.schemas").joinpath("policy.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(package().policy_manifest)


# --- registry kind --------------------------------------------------------------------------


def test_registry_accepts_a_stateful_v5_record_bound_to_a_manifest_digest() -> None:
    record = {
        **RECORD,
        "reference": "stateful-fake-v1",
        "kind": "stateful-v5",
        "coordinate_adapter": "manifest-bound",
        "policy_manifest_sha256": package().policy_manifest_sha256,
    }
    policy = ApprovedProviderPolicy.from_record(record)
    assert (
        policy.approval_sha256
        != ApprovedProviderPolicy.from_record(
            {**record, "policy_manifest_sha256": "0" * 64}
        ).approval_sha256
    )
    with pytest.raises(ApprovedProviderError, match="cannot run it"):
        resolve_approved_provider(
            {policy.reference: policy},
            policy.reference,
            approved_sha256=policy.approval_sha256,
            model=policy.model,
            prompt_version=policy.prompt_version,
            maximum_calls=policy.call_cap,
            provider_concurrency=1,
        )
    with pytest.raises(ApprovedProviderError, match="must bind a v5 policy_manifest_sha256"):
        ApprovedProviderPolicy.from_record({**record, "policy_manifest_sha256": None})
    with pytest.raises(ApprovedProviderError, match="manifest-bound"):
        ApprovedProviderPolicy.from_record({**record, "coordinate_adapter": "none"})
    with pytest.raises(ApprovedProviderError, match="only stateful-v5 records bind"):
        ApprovedProviderPolicy.from_record(
            {**RECORD, "policy_manifest_sha256": package().policy_manifest_sha256}
        )
