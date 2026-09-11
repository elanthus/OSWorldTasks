"""Regression matrices for the manifest-driven calibration runner."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5 import codex_cli_policy
from pixelgym.grounding.v5 import provider_adapters as adapters
from pixelgym.grounding.v5.calibration_runner import SpendSnapshot, run_calibration_plan
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import QWEN_STATEFUL_RETRY_SUCCESSOR, SpendLedger
from pixelgym.grounding.v5.plan import CalibrationPlan
from pixelgym.grounding.v5.provider_adapters import OpenRouterHttpAdapter
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
        cleanup: dict[str, bool] | None = None,
        accounting: dict[str, Any] | None = None,
    ) -> None:
        self.classifications = classifications
        self.spend = spend or SpendSnapshot.zero()
        self.crash = crash
        self.executed: list[int] = []
        self.reconciled: list[int] = []
        self.closed = False
        self.bound = False
        self.cleanup = cleanup or {"provider_adapter_closed": True}
        self.accounting = accounting or {}

    def bind_journal(self, journal: V5AttemptJournal) -> None:
        del journal
        self.bound = True

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

    def provider_accounting(self) -> dict[str, Any]:
        return self.accounting

    def transport_records(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        self.closed = True

    def cleanup_evidence(self) -> dict[str, bool]:
        return self.cleanup

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
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "tasks.json")
    _git(
        tmp_path,
        "-c",
        "user.name=PixelGym Tests",
        "-c",
        "user.email=pixelgym-tests@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Add task manifest",
    )
    revision = _git(tmp_path, "rev-parse", "HEAD")
    value: dict[str, Any] = {
        "schema_version": "pixelgym-agent-v5-runner-plan-v1",
        "purpose": "deterministic runner test",
        "code_revision": revision,
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
            **CallCaps(
                len(classifications), len(classifications), 0, len(classifications)
            ).to_dict(),
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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    _git(tmp_path, "add", "tasks.json")
    _git(
        tmp_path,
        "-c",
        "user.name=PixelGym Tests",
        "-c",
        "user.email=pixelgym-tests@example.invalid",
        "commit",
        "-q",
        "-m",
        "Tamper with task manifest",
    )
    value["code_revision"] = _git(tmp_path, "rev-parse", "HEAD")
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


def test_repository_state_checks_revision_and_tracked_cleanliness(tmp_path: Path) -> None:
    manifest_path, value = _plan_value(tmp_path, ["success_termination"])
    value["code_revision"] = "0" * 40
    mismatched = CalibrationPlan.from_dict(value)
    with pytest.raises(ValueError, match="code revision differs"):
        run_calibration_plan(
            tmp_path,
            plan=mismatched,
            approved_plan_sha256=mismatched.digest,
            adapter=FakeAdapter(["success_termination"]),
        )

    value["code_revision"] = _git(tmp_path, "rev-parse", "HEAD")
    manifest_path.write_text(manifest_path.read_text() + "\n", encoding="utf-8")
    dirty = CalibrationPlan.from_dict(value)
    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        run_calibration_plan(
            tmp_path,
            plan=dirty,
            approved_plan_sha256=dirty.digest,
            adapter=FakeAdapter(["success_termination"]),
        )


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


class JournalSpendAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(["success_termination"])
        self.ledger = SpendLedger(Decimal("1.00"), Decimal(0))

    def bind_journal(self, journal: V5AttemptJournal) -> None:
        self.ledger.bind_journal(journal)
        self.bound = True

    def execute(
        self,
        assignment: Any,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        del journal, approved_caps
        self.executed.append(assignment.ordinal)
        assert self.ledger.reserve_wire("request-0", Decimal("0.50"))
        assert self.ledger.record_cost("request-0", Decimal("0.20"), Decimal("0.50"))
        return self._result(assignment, "success_termination")

    def spend_snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            known_spend_usd=self.ledger.spent_usd,
            unknown_reservation_usd=(
                self.ledger.unknown_reservation_usd
                + self.ledger.in_flight_reservation_usd
            ),
            budget_accounted_spend_usd=self.ledger.budget_accounted_spend_usd,
            blocked=self.ledger.blocked,
        )


def test_resume_replays_spend_when_every_assignment_is_already_complete(
    tmp_path: Path,
) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    plan = CalibrationPlan.from_dict(value)
    original = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=JournalSpendAdapter(),
    )
    tampered = {**original, "spend": SpendSnapshot.zero().to_dict()}
    (tmp_path / "run/summary.json").write_text(
        json.dumps(tampered), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="completed summary spend does not match"):
        run_calibration_plan(
            tmp_path,
            plan=plan,
            approved_plan_sha256=plan.digest,
            adapter=JournalSpendAdapter(),
        )
    (tmp_path / "run/summary.json").unlink()

    resumed_adapter = JournalSpendAdapter()
    resumed = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=resumed_adapter,
    )

    assert resumed_adapter.executed == []
    assert resumed["spend"] == original["spend"]
    assert resumed["spend"] == {
        "blocked": False,
        "budget_accounted_spend_usd": "0.20",
        "known_spend_usd": "0.20",
        "unknown_reservation_usd": "0",
    }


def test_observed_provider_accounting_and_cleanup_are_written(tmp_path: Path) -> None:
    _, value = _plan_value(tmp_path, ["infrastructure_failure"])
    plan = CalibrationPlan.from_dict(value)
    summary = run_calibration_plan(
        tmp_path,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=FakeAdapter(
            ["infrastructure_failure"],
            spend=SpendSnapshot(Decimal(0), Decimal(0), Decimal(0), blocked=True),
            accounting={
                "unresolved_invocation_count": 1,
                "usage_telemetry_unavailable_count": 1,
            },
            cleanup={
                "provider_adapter_closed": True,
                "subprocesses_closed": False,
            },
        ),
    )

    assert summary["provider_accounting"]["unresolved_invocation_count"] == 1
    assert summary["provider_accounting"]["usage_telemetry_unavailable_count"] == 1
    assert summary["cleanup"]["subprocesses_closed"] is False


def test_codex_adapter_surfaces_unresolved_ledger_and_leaked_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, value = _plan_value(tmp_path, ["infrastructure_failure"])
    slot = codex_cli_policy.LUNA_LOW.slot
    value["policy_panel"][0]["slot"] = slot
    value["task_allocation"]["assignments"][0]["slot"] = slot
    value["provider"] = {
        "adapter": "codex_cli",
        "transport": "cli_subprocess",
        "config": {
            "invocation_journal": "codex-invocations.sqlite",
            "prior_budget_accounted_spend_usd": "0",
        },
    }
    plan = CalibrationPlan.from_dict(value)
    identity = codex_cli_policy.CodexRuntimeIdentity(
        cli_version=codex_cli_policy.CODEX_CLI_VERSION,
        authentication_mode=codex_cli_policy.AUTH_MODE,
        model=codex_cli_policy.LUNA_LOW.model,
        model_catalog_comp_hash=codex_cli_policy.LUNA_LOW.model_catalog_comp_hash,
        supported_reasoning_efforts=(codex_cli_policy.LUNA_LOW.model_reasoning_effort,),
        input_modalities=("text", "image"),
        context_window_tokens=codex_cli_policy.LUNA_LOW.model_context_window_tokens,
        exec_help_sha256="sha256:help",
        feature_inventory_sha256="sha256:features",
        configuration_preflight_validated=True,
    )
    monkeypatch.setattr(adapters, "probe_codex_runtime", lambda _config: identity)
    adapter = adapters.CodexCliAdapter(tmp_path, plan)
    assert adapter.ledger.reserve("unresolved")
    adapter.ledger.retain_unresolved_and_block("unresolved")

    class LeakedTransport:
        records: tuple[dict[str, Any], ...] = ()
        subprocesses_closed = False

        def close(self) -> None:
            return None

    adapter._transports[slot] = LeakedTransport()  # type: ignore[assignment]
    accounting = adapter.provider_accounting()
    adapter.close()

    assert accounting["unresolved_invocation_count"] == 1
    assert accounting["usage_telemetry_unavailable_count"] == 0
    assert adapter.cleanup_evidence()["subprocesses_closed"] is False


def test_openrouter_plan_request_maximum_mismatch_fails_before_dispatch(
    tmp_path: Path,
) -> None:
    _, value = _plan_value(tmp_path, ["success_termination"])
    slot = QWEN_STATEFUL_RETRY_SUCCESSOR.slot
    value["policy_panel"][0]["slot"] = slot
    value["task_allocation"]["assignments"][0]["slot"] = slot
    value["provider"] = {
        "adapter": "openrouter_http",
        "transport": "http",
        "config": {},
    }
    plan = CalibrationPlan.from_dict(value)

    with pytest.raises(ValueError, match="per-request theoretical maximum differs"):
        OpenRouterHttpAdapter(tmp_path, plan)

    value["budgets"]["per_request_theoretical_maximum_usd"] = str(
        QWEN_STATEFUL_RETRY_SUCCESSOR.request_maximum_usd
    )
    value["budgets"]["unknown_reservation_rule"] = "discard unknown charges"
    bad_rule_plan = CalibrationPlan.from_dict(value)
    with pytest.raises(ValueError, match="unknown reservation rule differs"):
        OpenRouterHttpAdapter(tmp_path, bad_rule_plan)


def test_stop_rules_cover_spend_hard_stops_and_unapproved_classifications(
    tmp_path: Path,
) -> None:
    _, spend_value = _plan_value(tmp_path, ["infrastructure_failure"])
    spend_plan = CalibrationPlan.from_dict(spend_value)
    spend_summary = run_calibration_plan(
        tmp_path,
        plan=spend_plan,
        approved_plan_sha256=spend_plan.digest,
        adapter=FakeAdapter(
            ["infrastructure_failure"],
            spend=SpendSnapshot(Decimal(0), Decimal(0), Decimal(0), blocked=True),
        ),
    )
    assert spend_summary["stop_reason"] == "spend_ledger_blocked"

    _, hard_value = _plan_value(tmp_path, ["policy_violation"])
    hard_value["outputs"]["directory"] = "hard-stop"
    hard_value["retry_breaker"]["continue_classifications"] = []
    hard_value["retry_breaker"]["hard_stop_classifications"] = ["policy_violation"]
    hard_plan = CalibrationPlan.from_dict(hard_value)
    hard_summary = run_calibration_plan(
        tmp_path,
        plan=hard_plan,
        approved_plan_sha256=hard_plan.digest,
        adapter=FakeAdapter(["policy_violation"]),
    )
    assert hard_summary["stop_reason"] == "hard_stop_classification:policy_violation"

    _, unknown_value = _plan_value(tmp_path, ["invalid_output"])
    unknown_value["outputs"]["directory"] = "unknown-classification"
    unknown_value["retry_breaker"]["continue_classifications"] = []
    unknown_plan = CalibrationPlan.from_dict(unknown_value)
    with pytest.raises(ValueError, match="has no approved continuation rule"):
        run_calibration_plan(
            tmp_path,
            plan=unknown_plan,
            approved_plan_sha256=unknown_plan.digest,
            adapter=FakeAdapter(["invalid_output"]),
        )


def test_resume_forbid_and_replayed_episode_identity_fail_closed(tmp_path: Path) -> None:
    _, forbid_value = _plan_value(tmp_path, ["success_termination"])
    forbid_value["outputs"]["resume_mode"] = "forbid"
    forbid_plan = CalibrationPlan.from_dict(forbid_value)
    (tmp_path / "run").mkdir()
    with pytest.raises(FileExistsError, match="refusing to replace runner output"):
        run_calibration_plan(
            tmp_path,
            plan=forbid_plan,
            approved_plan_sha256=forbid_plan.digest,
            adapter=FakeAdapter(["success_termination"]),
        )

    _, replay_value = _plan_value(tmp_path, ["success_termination"])
    replay_value["outputs"]["directory"] = "replay"
    replay_plan = CalibrationPlan.from_dict(replay_value)
    output = tmp_path / "replay"
    output.mkdir()
    journal = V5AttemptJournal(output / "attempts.sqlite")
    assignment = replay_plan.assignments[0]
    journal.append_event(
        event_key="campaign/assignment-0000/started",
        kind="campaign_assignment_started",
        trial_id=(f"manifest-{assignment.slot}-0000-{assignment.task_id}"),
        step_index=0,
        payload={"assignment": assignment.to_dict()},
    )
    wrong = FakeAdapter._result(assignment, "success_termination").to_dict()
    wrong["task_id"] = "wrong-task"
    journal.append_event(
        event_key="campaign/assignment-0000/completed",
        kind="campaign_assignment_completed",
        trial_id=(f"manifest-{assignment.slot}-0000-{assignment.task_id}"),
        step_index=0,
        payload={"episode_result": wrong},
    )
    journal.close()

    with pytest.raises(ValueError, match="wrong assignment"):
        run_calibration_plan(
            tmp_path,
            plan=replay_plan,
            approved_plan_sha256=replay_plan.digest,
            adapter=FakeAdapter(["success_termination"]),
        )


@pytest.mark.parametrize("enabled,code,blocked,expected", [
    (True, "transport_fault_retry_exhausted", False, 4),
    (False, "transport_fault_retry_exhausted", False, 1),
    (True, "different_infrastructure_failure", False, 1),
    (True, "transport_fault_retry_exhausted", True, 1),
])
def test_transport_exhaustion_continuation_is_narrow_and_preserves_failures(
    tmp_path, enabled, code, blocked, expected,
):
    classifications = ["infrastructure_failure"] * 3 + ["success_termination"]
    _, value = _plan_value(tmp_path, classifications)
    value["retry_breaker"]["continue_on_transport_retry_exhaustion"] = enabled
    value["retry_breaker"]["continue_classifications"].remove("infrastructure_failure")
    value["retry_breaker"]["hard_stop_classifications"] = ["infrastructure_failure"]
    value["retry_breaker"]["consecutive_failure_limit"] = 1
    plan = CalibrationPlan.from_dict(value)

    class ExhaustedAdapter(FakeAdapter):
        def execute(self, assignment, *, journal, approved_caps):
            result = super().execute(assignment, journal=journal, approved_caps=approved_caps)
            if result.classification == "infrastructure_failure":
                journal.append_event(
                    event_key=f"{result.trial_id}/exhausted", kind="sealed_unsuccessful_result",
                    trial_id=result.trial_id, step_index=0, payload={"failure_code": code},
                )
            return result

    summary = run_calibration_plan(
        tmp_path, plan=plan, approved_plan_sha256=plan.digest,
        adapter=ExhaustedAdapter(classifications, spend=SpendSnapshot(
            Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), blocked=blocked,
        )),
    )
    assert summary["attempted_policy_task_pairs"] == expected
    assert summary["classifications"]["infrastructure_failure"] == min(expected, 3)
    assert summary["completed_all_assigned_pairs"] == (expected == 4)
    assert summary["spend"]["unknown_reservation_usd"] == "0.2"
