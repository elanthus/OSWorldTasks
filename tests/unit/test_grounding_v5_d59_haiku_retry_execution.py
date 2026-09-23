from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.claude_code_policy import ClaudeRuntimeIdentity
from scripts.d59_haiku_retry_execution import (
    APPROVED_CAPS,
    APPROVED_RUNTIME_HOURS,
    EXECUTION_PLAN_DIGEST,
    EXECUTION_PLAN_PATH,
    OWNER_APPROVAL_PATH,
    execution_binding,
    expected_owner_approval,
    validate_assignments,
    validate_execution_authorization,
    validated_live_manifests,
)

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_PATH = ROOT / "artifacts/grounding-v5-haiku-cli-replication/snapshot.json"


def read(path: str | Path) -> dict[str, object]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_checked_in_approval_matches_exact_successor() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    approval = read(OWNER_APPROVAL_PATH)
    validate_execution_authorization(plan, approval)
    assert approval == expected_owner_approval(plan)
    assert approval["execution_plan_digest"] == EXECUTION_PLAN_DIGEST
    assert approval["approved_caps"] == APPROVED_CAPS.to_dict()
    assert approval["approved_runtime_window_hours"] == APPROVED_RUNTIME_HOURS
    assert approval["subscription_execution_authorized"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["approved_caps"].__setitem__("model_attempt_cap", 1),
            "authorization differs",
        ),
        (
            lambda value: value.__setitem__("approved_runtime_window_hours", 167),
            "authorization differs",
        ),
    ],
)
def test_authorization_rejects_cap_or_runtime_drift(
    mutation: object, message: str
) -> None:
    plan = read(EXECUTION_PLAN_PATH)
    approval = deepcopy(read(OWNER_APPROVAL_PATH))
    assert callable(mutation)
    mutation(approval)
    with pytest.raises(ValueError, match=message):
        validate_execution_authorization(plan, approval)


def test_all_successor_assignments_reproduce_the_approved_caps() -> None:
    jobs = validate_assignments(read(EXECUTION_PLAN_PATH))
    assert len(jobs) == 432
    assert all(str(job["trial_id"]).startswith("d59-haiku-r2-") for job in jobs)


def test_live_policy_sources_reproduce_the_successor_manifests() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    identity = ClaudeRuntimeIdentity(**calibration["runtime_identity"])
    manifests = validated_live_manifests(ROOT, plan, identity)
    assert {mode: manifest.policy_id for mode, manifest in manifests.items()} == {
        "history": "policy-6788af9cc24b21e0b89f",
        "stateless": "policy-9ce79eec1338f6426873",
    }


def test_successor_execution_binding_is_response_free() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    identity = ClaudeRuntimeIdentity(**calibration["runtime_identity"])
    binding = execution_binding(
        ROOT,
        plan=plan,
        approval=read(OWNER_APPROVAL_PATH),
        runtime_identity=identity,
    )
    assert binding["provider_calls_made_during_binding"] == 0
    assert binding["approved_caps"] == APPROVED_CAPS.to_dict()
