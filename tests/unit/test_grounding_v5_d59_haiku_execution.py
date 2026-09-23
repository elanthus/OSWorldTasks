from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.claude_code_policy import ClaudeRuntimeIdentity
from pixelgym.grounding.v5.d59_haiku_execution import (
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


def test_checked_in_owner_approval_matches_exact_frozen_plan() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    approval = read(OWNER_APPROVAL_PATH)
    validate_execution_authorization(plan, approval)
    assert approval == expected_owner_approval(plan)
    assert approval["execution_plan_digest"] == EXECUTION_PLAN_DIGEST
    assert approval["approved_caps"] == APPROVED_CAPS.to_dict()
    assert approval["approved_runtime_window_hours"] == APPROVED_RUNTIME_HOURS
    assert approval["subscription_execution_authorized"] is True


@pytest.mark.parametrize(
    ("target", "mutation", "message"),
    [
        (
            "plan",
            lambda value: value.__setitem__("execution_plan_digest", "sha256:" + "0" * 64),
            "digest is invalid",
        ),
        (
            "approval",
            lambda value: value["approved_caps"].__setitem__("model_attempt_cap", 1),
            "authorization differs",
        ),
        (
            "approval",
            lambda value: value.__setitem__("approved_runtime_window_hours", 167),
            "authorization differs",
        ),
    ],
)
def test_authorization_rejects_plan_cap_or_runtime_drift(
    target: str, mutation: object, message: str
) -> None:
    plan = read(EXECUTION_PLAN_PATH)
    approval = read(OWNER_APPROVAL_PATH)
    selected = plan if target == "plan" else approval
    damaged = deepcopy(selected)
    assert callable(mutation)
    mutation(damaged)
    if target == "plan":
        plan = damaged
    else:
        approval = damaged
    with pytest.raises(ValueError, match=message):
        validate_execution_authorization(plan, approval)


def test_all_assignments_reproduce_the_approved_caps() -> None:
    jobs = validate_assignments(read(EXECUTION_PLAN_PATH))
    assert len(jobs) == 432
    assert sum(job["phase"] == "primary" for job in jobs) == 384
    assert sum(job["phase"] == "reliability" for job in jobs) == 48
    assert len({job["trial_id"] for job in jobs}) == len(jobs)


def test_superseded_execution_rejects_changed_live_policy_sources() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    identity = ClaudeRuntimeIdentity(**calibration["runtime_identity"])
    with pytest.raises(ValueError, match="live history policy differs"):
        validated_live_manifests(ROOT, plan, identity)


def test_historical_execution_binding_remains_response_free() -> None:
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
