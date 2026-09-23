from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5.d59_haiku_freeze import (
    ADMISSION_PATH,
    CALIBRATION_PATH,
    HISTORY_POLICY_ID,
    SELECTION_PATH,
    STATELESS_POLICY_ID,
    execution_plan,
    expected_outputs,
    owner_exception,
)
from scripts.prepare_grounding_v5_d59_haiku_freeze import write_outputs

ROOT = Path(__file__).resolve().parents[2]
RECORDED_SOURCE_REVISION = "e039ccdf9115dece43f5ace3390ffdce4d0ea703"


def read(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_owner_exception_is_narrow_and_non_executable() -> None:
    value = owner_exception()
    assert value["owner_statement"] == "exception approved"
    assert value["scope"]["history_policy_id"] == HISTORY_POLICY_ID
    assert value["scope"]["stateless_policy_id"] == STATELESS_POLICY_ID
    assert value["scope"]["os_sandbox_applied"] is False
    assert value["scope"]["cli_restrictions_required"] is True
    assert value["execution_boundary"] == {
        "execution_enabled": False,
        "approved_model_attempt_cap": 0,
        "approved_provider_wire_request_cap": 0,
        "subscription_execution_authorized": False,
        "runtime_window_approved": False,
        "provider_calls_made": 0,
    }
    assert "OS-enforced" in value["claim_boundary"]


def test_execution_plan_reuses_admitted_tasks_and_exact_selected_policies() -> None:
    value = execution_plan(ROOT, source_revision="a" * 40)
    assert value["execution_enabled"] is False
    assert value["subscription_execution_authorized"] is False
    assert value["approved_model_attempt_cap"] == 0
    assert value["approved_provider_wire_request_cap"] == 0
    assert value["task_manifest_binding"]["path"].endswith("task-manifest.json")
    assert value["admission_binding"]["path"] == ADMISSION_PATH
    assert {item["policy_id"] for item in value["policy_manifests"].values()} == {
        HISTORY_POLICY_ID,
        STATELESS_POLICY_ID,
    }
    assert value["security_boundary"]["os_sandbox_applied"] == {
        "history": False,
        "stateless": False,
    }
    assert value["phase_caps"] == {
        "primary": {
            "environment_action_cap": 10830,
            "model_attempt_cap": 21660,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 21660,
        },
        "reliability": {
            "environment_action_cap": 1304,
            "model_attempt_cap": 2608,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 2608,
        },
        "aggregate": {
            "environment_action_cap": 12134,
            "model_attempt_cap": 24268,
            "provider_control_request_cap": 0,
            "provider_wire_request_cap": 24268,
        },
    }
    assert value["runtime"]["proposed_aggregate_window_hours"] == 168
    assert value["runtime"]["approval_required"] is True
    assert value["budget"]["aggregate_planning_cap_usd"] == "0.00"
    assert len(value["primary_jobs"]) == 384
    assert len(value["reliability_jobs"]) == 48
    assert all(
        item["trial_id"].startswith("d59-haiku-")
        for item in [*value["primary_jobs"], *value["reliability_jobs"]]
    )


@pytest.mark.parametrize(
    ("path", "mutation", "message"),
    [
        (
            SELECTION_PATH,
            lambda value: value["selected_pair"].__setitem__("model", "different"),
            "policy pair changed",
        ),
        (
            CALIBRATION_PATH,
            lambda value: value["policy_manifests"]["history"]["sandbox"][
                "runtime_enforcement"
            ].__setitem__("os_sandbox_applied", True),
            "expects the disclosed unsandboxed policy",
        ),
    ],
)
def test_execution_plan_rejects_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    mutation: object,
    message: str,
) -> None:
    original = read(path)
    damaged = deepcopy(original)
    assert callable(mutation)
    mutation(damaged)
    replacement = tmp_path / Path(path).name
    replacement.write_text(json.dumps(damaged), encoding="utf-8")
    original_read = Path.read_text

    def substituted_read(self: Path, *args: object, **kwargs: object) -> str:
        if self == ROOT / path:
            return replacement.read_text(encoding="utf-8")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", substituted_read)
    with pytest.raises(ValueError, match=message):
        execution_plan(ROOT, source_revision="b" * 40)


def test_expected_outputs_are_response_free_and_write_once(tmp_path: Path) -> None:
    outputs = expected_outputs(ROOT, source_revision="c" * 40)
    assert set(outputs) == {"owner-exception.json", "execution-plan.json"}
    assert all(b"provider_calls_made" in payload for payload in outputs.values())
    public = tmp_path / "freeze"
    write_outputs(outputs, verify=False, public=public)
    write_outputs(outputs, verify=True, public=public)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        write_outputs(outputs, verify=False, public=public)


def test_checked_in_freeze_reproduces_from_recorded_source_revision() -> None:
    outputs = expected_outputs(ROOT, source_revision=RECORDED_SOURCE_REVISION)
    public = ROOT / "artifacts/grounding-v5-d59-haiku-freeze"
    for name, payload in outputs.items():
        assert (public / name).read_bytes() == payload
