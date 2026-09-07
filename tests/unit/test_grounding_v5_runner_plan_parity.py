"""Frozen pre-refactor parity projections for representative D5.6 plans.

The `episode_results`/`classifications`/`outcome_denominators` values embedded in
`tests/unit/fixtures/grounding_v5_runner_plan_parity.json` were originally cross-checked
against `legacy.grounding.v5.d56_claude_subscription_campaign`,
`d56_codex_cli_calibration`, and `d56_qwen_full_calibration`'s
`legacy_runner_result_projection` functions; that recomputation is dropped here (issue
#170) since the fixture already stores their output. See the fixture's `_provenance`
field for the generating command and revision.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pixelgym.grounding.v5.calibration_runner import SpendSnapshot, run_calibration_plan
from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.plan import (
    CalibrationPlan,
    legacy_plan_projection,
    legacy_summary_classifications,
)
from pixelgym.grounding.v5.runner import EpisodeResult

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/unit/fixtures/grounding_v5_runner_plan_parity.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_representative_d56_plans_match_frozen_pre_refactor_projections() -> None:
    fixture = _load(FIXTURE)
    assert fixture["schema_version"] == "pixelgym-grounding-runner-parity-fixture-v1"

    for expected in fixture["plans"].values():
        plan = _load(ROOT / expected["source_plan"])
        summary = _load(ROOT / expected["source_summary"])
        projection = legacy_plan_projection(plan)

        assert projection.canonical_plan_sha256 == expected["canonical_plan_sha256"]
        assert [list(item) for item in projection.task_assignment] == expected["task_assignment"]
        assert list(projection.stop_rules) == expected["stop_rules"]
        assert legacy_summary_classifications(summary) == expected["summary_classifications"]


class DeterministicParityAdapter:
    adapter_name = "deterministic_fake"
    transport_name = "deterministic_fake"

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.executed: list[int] = []
        self.closed = False

    def bind_journal(self, journal: V5AttemptJournal) -> None:
        del journal

    def execute(
        self,
        assignment: Any,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        del journal, approved_caps
        self.executed.append(assignment.ordinal)
        expected = self.results[assignment.ordinal]
        return EpisodeResult(
            **{key: value for key, value in expected.items() if key != "slot"}
        )

    def reconcile(
        self,
        assignment: Any,
        *,
        journal: V5AttemptJournal,
        approved_caps: CallCaps,
    ) -> EpisodeResult:
        raise AssertionError((assignment, journal, approved_caps))

    def spend_snapshot(self) -> SpendSnapshot:
        return SpendSnapshot.zero()

    def provider_accounting(self) -> dict[str, Any]:
        return {}

    def transport_records(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        self.closed = True

    def cleanup_evidence(self) -> dict[str, bool]:
        return {"provider_adapter_closed": self.closed}


PANELS = ("claude_subscription", "codex_cli", "openrouter_http")


@pytest.mark.parametrize("panel", PANELS)
def test_manifest_runner_matches_legacy_result_aggregation(
    tmp_path: Path, panel: str
) -> None:
    fixture = _load(FIXTURE)["plans"][panel]
    execution = fixture["runner_execution"]
    root = tmp_path / panel
    root.mkdir()
    plan = _runner_plan(root, fixture, execution)
    adapter = DeterministicParityAdapter(execution["episode_results"])

    summary = run_calibration_plan(
        root,
        plan=plan,
        approved_plan_sha256=plan.digest,
        adapter=adapter,
    )

    assert summary["episode_results"] == execution["episode_results"]
    assert summary["classifications"] == execution["classifications"]
    assert summary["outcome_denominators"] == execution["outcome_denominators"]


def _runner_plan(
    root: Path, fixture: dict[str, Any], execution: dict[str, Any]
) -> CalibrationPlan:
    records = [
        {
            "task_id": task_id,
            "seed_record": {"seed": seed, "family": family},
        }
        for seed, task_id, _limit, family in fixture["task_assignment"]
    ]
    manifest_body = {"records": records}
    manifest = {**manifest_body, "manifest_digest": content_digest(manifest_body)}
    (root / "tasks.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "tasks.json")
    _git(
        root,
        "-c",
        "user.name=PixelGym Tests",
        "-c",
        "user.email=pixelgym-tests@example.invalid",
        "commit",
        "-q",
        "-m",
        "Add parity task manifest",
    )
    slot = execution["slot"]
    policy_manifest = {
        "policy_id": f"policy-{slot}",
        "max_model_attempts_per_action": 1,
        "max_cancellation_requests_per_attempt": 0,
        "max_reconciliation_requests_per_attempt": 0,
    }
    assignments = [
        {
            "ordinal": ordinal,
            "slot": slot,
            "seed": seed,
            "task_id": task_id,
            "family": family,
            "action_limit": limit,
        }
        for ordinal, (seed, task_id, limit, family) in enumerate(
            fixture["task_assignment"]
        )
    ]
    action_cap = sum(item["action_limit"] for item in assignments)
    value = {
        "schema_version": "pixelgym-agent-v5-runner-plan-v1",
        "purpose": f"legacy parity for {slot}",
        "code_revision": _git(root, "rev-parse", "HEAD"),
        "policy_panel": [
            {
                "slot": slot,
                "policy_manifest": policy_manifest,
                "policy_manifest_digest": content_digest(policy_manifest),
            }
        ],
        "task_allocation": {
            "manifest_path": "tasks.json",
            "manifest_digest": manifest["manifest_digest"],
            "assignments": assignments,
        },
        "provider": {
            "adapter": "deterministic_fake",
            "transport": "deterministic_fake",
            "config": {},
        },
        "budgets": {
            **CallCaps(action_cap, action_cap, 0, action_cap).to_dict(),
            "maximum_spend_usd": "1.00",
            "per_request_theoretical_maximum_usd": "0.00",
            "unknown_reservation_rule": "retain every unknown reservation",
        },
        "retry_breaker": {
            "max_bounded_retries_per_action": 0,
            "consecutive_failure_limit": len(assignments) + 1,
            "continue_classifications": ["step_limit_truncation"],
            "hard_stop_classifications": ["invalid_output"],
        },
        "stop_conditions": list(fixture["stop_rules"]),
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
    return CalibrationPlan.from_dict(value)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
