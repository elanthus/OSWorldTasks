"""Regression matrices for the manifest-driven calibration runner."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import calibration_runner
from pixelgym.grounding.v5.calibration_runner import SpendSnapshot, run_calibration_plan
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.runner import EpisodeResult
from scripts import run_grounding_v5_calibration


class FakeAdapter:
    adapter_name = "deterministic_fake"
    transport_name = "deterministic_fake"

    def __init__(
        self,
        classifications: list[str],
        *,
        spend: SpendSnapshot | None = None,
        crash: bool = False,
    ) -> None:
        self.classifications = classifications
        self.spend = spend or SpendSnapshot.zero()
        self.crash = crash
        self.executed: list[int] = []
        self.reconciled: list[int] = []
        self.closed = False

    def execute(
        self,
        assignment: Any,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        del journal, approved_caps
        self.executed.append(assignment.ordinal)
        if self.crash:
            raise RuntimeError("synthetic crash")
        return self._result(assignment, self.classifications[assignment.ordinal])

    def reconcile(
        self,
        assignment: Any,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        del journal, approved_caps
        self.reconciled.append(assignment.ordinal)
        return self._result(assignment, self.classifications[assignment.ordinal])

    def spend_snapshot(self) -> SpendSnapshot:
        return self.spend

    def transport_records(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _result(assignment: Any, classification: str) -> EpisodeResult:
        return EpisodeResult(
            trial_id=(
                f"manifest-{assignment.slot}-{assignment.ordinal:04d}-{assignment.task_id}"
            ),
            task_id=assignment.task_id,
            success=classification == "success_termination",
            classification=classification,
            environment_actions=1,
            model_attempts=1,
            provider_control_requests=0,
            provider_wire_requests=1,
            final_policy_checkpoint_digest="sha256:" + "a" * 64,
        )


def _plan_value(tmp_path: Path, classifications: list[str]) -> tuple[Path, dict[str, Any]]:
    policy_manifest = {
        "policy_id": "policy-test",
        "max_model_attempts_per_action": 1,
        "max_cancellation_requests_per_attempt": 0,
        "max_reconciliation_requests_per_attempt": 0,
    }
    records = [
        {
            "task_id": f"task-{index}",
            "seed_record": {"seed": index + 1, "family": "evidence_aggregation"},
        }
        for index in range(len(classifications))
    ]
    manifest_body = {"records": records}
    manifest = {**manifest_body, "manifest_digest": content_digest(manifest_body)}
    manifest_path = tmp_path / "tasks.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    value: dict[str, Any] = {
        "schema_version": "pixelgym-agent-v5-runner-plan-v1",
        "purpose": "deterministic runner test",
        "code_revision": "1" * 40,
        "policy_panel": [
            {
                "slot": "fake",
                "policy_manifest": policy_manifest,
                "policy_manifest_digest": content_digest(policy_manifest),
            }
        ],
        "task_allocation": {
            "manifest_path": "tasks.json",
            "manifest_digest": manifest["manifest_digest"],
            "assignments": [
                {
                    "ordinal": index,
                    "slot": "fake",
                    "seed": index + 1,
                    "task_id": f"task-{index}",
                    "family": "evidence_aggregation",
                    "action_limit": 1,
                }
                for index in range(len(classifications))
            ],
        },
        "provider": {
            "adapter": "deterministic_fake",
            "transport": "deterministic_fake",
            "config": {},
        },
        "budgets": {
            **CallCaps(len(classifications), len(classifications), 0, len(classifications)).to_dict(),
            "maximum_spend_usd": "1.00",
            "per_request_theoretical_maximum_usd": "0.00",
            "unknown_reservation_rule": "retain every unknown reservation",
        },
        "retry_breaker": {
            "max_bounded_retries_per_action": 0,
            "consecutive_failure_limit": len(classifications) + 1,
            "continue_classifications": sorted(set(classifications)),
            "hard_stop_classifications": [],
        },
        "stop_conditions": ["stop before a call that exceeds any approved cap"],
        "outputs": {
            "directory": "run",
            "journal": "attempts.sqlite",
            "summary": "summary.json",
            "resume_mode": "reconcile_existing",
        },
        "requires_clean_tracked_worktree": True,
        "provider_calls_made_while_planning": 0,
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }
    return manifest_path, value


@pytest.fixture(autouse=True)
def _skip_repository_git_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibration_runner, "_verify_repository_state", lambda *_: None)


def test_plan_rejects_call_caps_that_do_not_match_assignments(tmp_path: Path) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    value["budgets"]["model_attempt_cap"] = 2

    with pytest.raises(ValueError, match="declared call caps differ"):
        CalibrationPlan.from_dict(value)


def test_plan_rejects_provider_transport_contract_merging(tmp_path: Path) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    value["provider"] = {"adapter": "codex_cli", "transport": "http", "config": {}}

    with pytest.raises(ValueError, match="adapter and transport kind disagree"):
        CalibrationPlan.from_dict(value)


def test_runner_recomputes_task_manifest_digest(tmp_path: Path) -> None:
    manifest_path, value = _plan_value(tmp_path, ["success_termination"])
    manifest = json.loads(manifest_path.read_text())
    manifest["records"][0]["unapproved_edit"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = CalibrationPlan.from_dict(value)
    adapter = FakeAdapter(["success_termination"])

    with pytest.raises(ValueError, match=r"task manifest digest mismatch: tasks\.json"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256=plan.digest,
            adapter=adapter,
        )
    assert adapter.executed == []
    assert not (tmp_path / "run").exists()


def test_single_cli_validates_without_constructing_a_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(value), encoding="utf-8")

    run_grounding_v5_calibration.main(["--validate-only", "--plan", str(plan_path)])

    reported = json.loads(capsys.readouterr().out)
    assert reported["plan_sha256"] == CalibrationPlan.from_dict(value).digest
    assert reported["provider_adapter"] == "deterministic_fake"


@pytest.mark.parametrize(
    ("classifications", "expected"),
    [
        (["success_termination"], {"attempted": 1}),
        (["invalid_output"], {"attempted": 1, "invalid_output": 1}),
        (
            ["infrastructure_failure"],
            {"attempted": 1, "infrastructure_failure": 1},
        ),
        (["policy_violation"], {"attempted": 1, "policy_violation": 1}),
    ],
)
def test_outcome_denominator_matrix(
    tmp_path: Path, classifications: list[str], expected: dict[str, int]
) -> None:
    _, value = _plan_value(tmp_path, classifications)
    plan = CalibrationPlan.from_dict(value)
    summary = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=FakeAdapter(classifications),
    )

    for key in ("attempted", "invalid_output", "infrastructure_failure", "policy_violation"):
        assert summary["outcome_denominators"][key] == expected.get(key, 0)


def test_crash_resume_reconciles_without_reexecuting_assignment(tmp_path: Path) -> None:
    _, value = _plan_value(tmp_path, ["step_limit_truncation"])
    plan = CalibrationPlan.from_dict(value)
    crashing = FakeAdapter(["step_limit_truncation"], crash=True)

    with pytest.raises(RuntimeError, match="synthetic crash"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256=plan.digest,
            adapter=crashing,
        )
    incomplete = json.loads((tmp_path / "run/summary.json").read_text())
    assert incomplete["run_state"] == "incomplete"
    assert incomplete["outcome_denominators"]["attempted"] == 1

    resumed = FakeAdapter(["step_limit_truncation"])
    summary = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=resumed,
    )
    assert resumed.executed == []
    assert resumed.reconciled == [0]
    assert summary["run_state"] == "complete"


def test_completed_summary_is_idempotent_and_mismatched_digest_fails_closed(
    tmp_path: Path,
) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    plan = CalibrationPlan.from_dict(value)
    first = FakeAdapter(["success_termination"])
    summary = run_calibration_plan(
        tmp_path, plan=plan, approved_plan_sha256=plan.digest, adapter=first
    )
    second = FakeAdapter(["infrastructure_failure"])
    assert (
        run_calibration_plan(
            tmp_path, plan=plan, approved_plan_sha256=plan.digest, adapter=second
        )
        == summary
    )
    assert second.executed == [] and second.closed

    (tmp_path / "run/summary.json").write_text(
        json.dumps({"approved_plan_sha256": plan.digest, "run_state": "complete"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="completed summary schema_version mismatch"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256=plan.digest,
            adapter=FakeAdapter(["success_termination"]),
        )
    (tmp_path / "run/summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (tmp_path / "run/attempts.sqlite").unlink()
    with pytest.raises(FileNotFoundError, match="completed summary journal is missing"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256=plan.digest,
            adapter=FakeAdapter(["success_termination"]),
        )

    with pytest.raises(ValueError, match="approved runner plan digest"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256="sha256:" + "0" * 64,
            adapter=FakeAdapter(["success_termination"]),
        )


def test_unknown_spend_reservation_counts_against_cap_and_overage_fails_closed(
    tmp_path: Path,
) -> None:
    _, value = _plan_value(tmp_path, ["infrastructure_failure"])
    plan = CalibrationPlan.from_dict(value)
    within = SpendSnapshot(Decimal("0.20"), Decimal("0.80"), Decimal("1.00"))
    summary = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=FakeAdapter(["infrastructure_failure"], spend=within),
    )
    assert summary["spend"]["unknown_reservation_usd"] == "0.80"
    assert summary["spend"]["budget_accounted_spend_usd"] == "1.00"

    _, second_value = _plan_value(tmp_path, ["infrastructure_failure"])
    second_value["outputs"]["directory"] = "over-cap"
    second_plan = CalibrationPlan.from_dict(second_value)
    over = replace(within, budget_accounted_spend_usd=Decimal("1.01"))
    with pytest.raises(RuntimeError, match="exceeds the approved maximum"):
        run_calibration_plan(
            tmp_path,
            plan=second_plan,
            approved_plan_sha256=second_plan.digest,
            adapter=FakeAdapter(["infrastructure_failure"], spend=over),
        )


def test_consecutive_failure_breaker_preserves_attempted_denominator(tmp_path: Path) -> None:
    classifications = ["invalid_output", "infrastructure_failure", "success_termination"]
    _, value = _plan_value(tmp_path, classifications)
    value["retry_breaker"]["consecutive_failure_limit"] = 2
    plan = CalibrationPlan.from_dict(value)
    summary = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=FakeAdapter(classifications),
    )

    assert summary["attempted_policy_task_pairs"] == 2
    assert summary["completed_all_assigned_pairs"] is False
    assert summary["stop_reason"] == "consecutive_failure_breaker"
