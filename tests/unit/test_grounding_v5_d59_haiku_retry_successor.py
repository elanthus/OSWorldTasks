from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_retry_successor import (
    DISCARDED_RUN_PATH,
    PREDECESSOR_PLAN_DIGEST,
    PREDECESSOR_PLAN_PATH,
    execution_plan,
    expected_outputs,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDED_SOURCE_REVISION = "f965d65380a230113327b6c3c637787af35a88d3"
PUBLIC = ROOT / "artifacts/grounding-v5-d59-haiku-api-retry-successor"


def read(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_discarded_run_receipt_is_response_free_and_preserves_failure() -> None:
    value = read(DISCARDED_RUN_PATH)
    body = {key: item for key, item in value.items() if key != "receipt_digest"}
    assert value["receipt_digest"] == content_digest(body)
    assert value["execution_plan_digest"] == PREDECESSOR_PLAN_DIGEST
    assert value["disposition"] == "discarded_from_scoring"
    assert value["restart_or_replay_performed"] is False
    assert value["provider_response_content_retained"] is False
    assert value["cohort_state"] == {
        "assigned": 432,
        "attempted": 1,
        "completed": 1,
        "unrun": 431,
    }
    assert value["observed_usage"] == {
        "cli_internal_api_retry_events": 2,
        "minimum_provider_api_attempts": 20,
        "provider_control_requests": 0,
        "runner_model_attempts": 18,
        "runner_provider_wire_requests": 18,
        "unresolved_invocations": 0,
    }
    assert value["policy_violation"]["code"] == ("unauthorized_system_event:api_retry")
    serialized = json.dumps(value, sort_keys=True)
    for forbidden in ("raw_stdout", "raw_stderr", "response_text", "session_id"):
        assert forbidden not in serialized


def test_successor_is_fresh_non_executable_and_zero_retry() -> None:
    discarded = read(DISCARDED_RUN_PATH)
    predecessor = read(PREDECESSOR_PLAN_PATH)
    value = execution_plan(
        ROOT,
        source_revision=RECORDED_SOURCE_REVISION,
        discarded_run=discarded,
    )
    assert value["execution_enabled"] is False
    assert value["subscription_execution_authorized"] is False
    assert value["approved_model_attempt_cap"] == 0
    assert value["approved_provider_wire_request_cap"] == 0
    assert value["provider_calls_made_during_successor_preparation"] == 0
    assert value["predecessor"]["outcomes_reused"] == 0
    assert value["predecessor"]["assignments_replayed"] == 0
    assert value["change_control"]["cli_api_retry_limit"] == 0
    assert value["change_control"]["fail_closed_if_api_retry_observed"] is True
    assert value["phase_caps"] == predecessor["phase_caps"]
    assert value["runtime"] == predecessor["runtime"]
    assert len(value["primary_jobs"]) == 384
    assert len(value["reliability_jobs"]) == 48
    jobs = [*value["primary_jobs"], *value["reliability_jobs"]]
    assert all(item["trial_id"].startswith("d59-haiku-r2-") for item in jobs)
    old_policy_ids = {item["policy_id"] for item in predecessor["policy_manifests"].values()}
    for manifest in value["policy_manifests"].values():
        assert manifest["policy_id"] not in old_policy_ids
        inference = dict(manifest["inference_parameters"])
        assert inference["cli_api_retry_limit"] == "0"
        assert inference["cli_api_retry_environment_variable"] == "CLAUDE_CODE_MAX_RETRIES"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.__setitem__("execution_plan_digest", "sha256:" + "0" * 64),
            "does not bind the predecessor plan",
        ),
        (
            lambda value: value.__setitem__("disposition", "scored"),
            "does not preserve the failure boundary",
        ),
        (
            lambda value: value["observed_usage"].__setitem__("minimum_provider_api_attempts", 18),
            "usage accounting changed",
        ),
        (
            lambda value: value["policy_violation"].__setitem__("code", "other"),
            "policy violation changed",
        ),
    ],
)
def test_successor_rejects_discarded_run_drift(mutation: object, message: str) -> None:
    discarded = deepcopy(read(DISCARDED_RUN_PATH))
    assert callable(mutation)
    mutation(discarded)
    with pytest.raises(ValueError, match=message):
        execution_plan(ROOT, source_revision="a" * 40, discarded_run=discarded)


def test_checked_in_successor_reproduces_from_recorded_source_revision() -> None:
    discarded = read(DISCARDED_RUN_PATH)
    outputs = expected_outputs(
        ROOT,
        source_revision=RECORDED_SOURCE_REVISION,
        discarded_run=discarded,
    )
    assert set(outputs) == {"discarded-run.json", "execution-plan.json"}
    for name, payload in outputs.items():
        assert (PUBLIC / name).read_bytes() == payload
