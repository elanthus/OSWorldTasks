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
def test_authorization_rejects_cap_or_runtime_drift(mutation: object, message: str) -> None:
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


def test_superseded_successor_rejects_changed_live_policy_sources() -> None:
    plan = read(EXECUTION_PLAN_PATH)
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    identity = ClaudeRuntimeIdentity(**calibration["runtime_identity"])
    with pytest.raises(ValueError, match="live history policy differs"):
        validated_live_manifests(ROOT, plan, identity)


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


@pytest.mark.parametrize("runtime_limit", [None, 1])
def test_successor_entry_rejects_transport_retry_drift(monkeypatch, runtime_limit):
    from scripts import run_grounding_v5_d59_haiku_retry_successor as entry

    plan = read(EXECUTION_PLAN_PATH)
    identity = ClaudeRuntimeIdentity(**read(CALIBRATION_PATH)["runtime_identity"])
    # Explicit policy fixtures isolate the transport binding from historical source drift.
    from types import SimpleNamespace

    manifests = {
        mode: SimpleNamespace(inference_parameters=(("cli_api_retry_limit", "0"),))
        for mode in ("history", "stateless")
    }
    monkeypatch.setattr(entry.authorization, "validated_live_manifests", lambda *args: manifests)
    monkeypatch.setattr(entry.runner, "CLI_API_RETRY_LIMIT", runtime_limit)
    with pytest.raises(ValueError, match="retry limit differs"):
        entry.validated_transport_manifests(ROOT, plan, identity)


@pytest.mark.parametrize("limits,runtime_limit", [(("0", "0"), 0), ((None, None), None)])
def test_successor_entry_accepts_matching_transport_limits(monkeypatch, limits, runtime_limit):
    from types import SimpleNamespace

    from scripts import run_grounding_v5_d59_haiku_retry_successor as entry

    manifests = {
        mode: SimpleNamespace(
            inference_parameters=(() if limit is None else (("cli_api_retry_limit", limit),))
        )
        for mode, limit in zip(("history", "stateless"), limits, strict=True)
    }
    monkeypatch.setattr(entry.authorization, "validated_live_manifests", lambda *args: manifests)
    monkeypatch.setattr(entry.runner, "CLI_API_RETRY_LIMIT", runtime_limit)
    identity = ClaudeRuntimeIdentity(**read(CALIBRATION_PATH)["runtime_identity"])
    assert (
        entry.validated_transport_manifests(
            ROOT, read(EXECUTION_PLAN_PATH), runtime_identity=identity
        )
        == manifests
    )


def test_successor_entry_checks_every_manifest(monkeypatch):
    from types import SimpleNamespace

    from scripts import run_grounding_v5_d59_haiku_retry_successor as entry

    manifests = {
        "history": SimpleNamespace(inference_parameters=(("cli_api_retry_limit", "0"),)),
        "stateless": SimpleNamespace(inference_parameters=(("cli_api_retry_limit", "1"),)),
    }
    monkeypatch.setattr(entry.authorization, "validated_live_manifests", lambda *args: manifests)
    monkeypatch.setattr(entry.runner, "CLI_API_RETRY_LIMIT", 0)
    identity = ClaudeRuntimeIdentity(**read(CALIBRATION_PATH)["runtime_identity"])
    with pytest.raises(ValueError, match="retry limit differs"):
        entry.validated_transport_manifests(ROOT, read(EXECUTION_PLAN_PATH), identity)
