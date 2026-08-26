from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from pixelgym.grounding.v5 import expanded_calibration
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.openrouter_policy import MODEL
from pixelgym.grounding.v5.runner import TransportOutcome

ROOT = Path(__file__).parents[2]
REAL_COMPLETED_PILOT_EVIDENCE = expanded_calibration._completed_pilot_evidence


def _pilot_evidence() -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    return {
        "approved_plan_sha256": expanded_calibration.APPROVED_PILOT_PLAN_DIGEST,
        "plan_path": expanded_calibration.PILOT_PLAN.as_posix(),
        "plan_sha256": digest,
        "summary_path": expanded_calibration.PILOT_SUMMARY.as_posix(),
        "summary_sha256": digest,
        "journal_path": expanded_calibration.PILOT_JOURNAL.as_posix(),
        "journal_sha256": digest,
        "provider_wire_requests": 20,
        "actual_aggregate_spend_usd": "0.004228237",
        "journal_integrity": {
            "event_count": 150,
            "object_count": 185,
            "event_chain_digest": digest,
        },
    }


@pytest.fixture(autouse=True)
def _stable_completed_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        expanded_calibration,
        "_completed_pilot_evidence",
        lambda _root: _pilot_evidence(),
    )


class InvalidOutputTransport:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self.wire_requests_sent = 0
        self.spent_usd = expanded_calibration.PRIOR_AGGREGATE_SPEND_USD

    def send(
        self, request: dict[str, object], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del request, deadline_seconds
        self.wire_requests_sent += 1
        cost = Decimal("0.0001")
        self.spent_usd += cost
        self.records.append(
            {"idempotency_key": idempotency_key, "status": "response", "cost_usd": str(cost)}
        )
        return TransportOutcome(
            "response",
            {
                "response_id": "fake-invalid",
                "model": MODEL,
                "content": "not-json",
                "finish_reason": "stop",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 1,
                    "cost": str(cost),
                    "upstream_provider": "Alibaba",
                    "price_guard": "ok",
                },
            },
        )

    def cancel(
        self, *, idempotency_key: str, mode: str
    ) -> Literal["cancelled", "unknown"]:
        del idempotency_key, mode
        return "unknown"

    def reconcile(
        self, *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        del idempotency_key, deadline_seconds
        return TransportOutcome("unknown", failure_code="disabled")


def test_completed_pilot_evidence_recomputes_journal_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"pilot": "approved"}
    journal_path = tmp_path / "pilot/attempts.sqlite"
    journal_path.parent.mkdir()
    journal = V5AttemptJournal(journal_path)
    integrity = journal.integrity_report()
    journal.close()
    summary = {
        "approved_plan_sha256": content_digest(plan),
        "provider_wire_requests": 20,
        "actual_aggregate_spend_usd": "0.004228237",
        "journal_integrity": integrity,
    }
    (tmp_path / "pilot-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (tmp_path / "pilot/summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(expanded_calibration, "PILOT_PLAN", Path("pilot-plan.json"))
    monkeypatch.setattr(expanded_calibration, "PILOT_SUMMARY", Path("pilot/summary.json"))
    monkeypatch.setattr(expanded_calibration, "PILOT_JOURNAL", Path("pilot/attempts.sqlite"))
    monkeypatch.setattr(
        expanded_calibration, "APPROVED_PILOT_PLAN_DIGEST", content_digest(plan)
    )

    evidence = REAL_COMPLETED_PILOT_EVIDENCE(tmp_path)

    assert evidence["journal_integrity"] == integrity


def test_expanded_plan_runs_full_horizons_within_existing_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(expanded_calibration, "_git", lambda *_args: "revision-1")

    plan = expanded_calibration.build_plan(ROOT)

    assert plan["provider_calls_made"] == 0
    assert plan["model"] == MODEL
    assert len(plan["tasks"]) == 10
    assert sum(task["max_episode_steps"] for task in plan["tasks"]) == 261
    assert plan["caps"]["model_attempt_cap"] == 261
    assert plan["caps"]["provider_wire_request_cap"] == 261
    assert plan["caps"]["provider_control_request_cap"] == 0
    assert Decimal(plan["caps"]["aggregate_upper_bound_usd"]) == Decimal(
        "4.430654605"
    )
    assert Decimal(plan["caps"]["aggregate_headroom_usd"]) == Decimal(
        "0.569345395"
    )


def test_expanded_run_rejects_wrong_digest_before_any_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(expanded_calibration, "_git", lambda *_args: "revision-1")
    plan = expanded_calibration.build_plan(ROOT)
    transport = InvalidOutputTransport()

    with pytest.raises(ValueError, match="approved expanded-run plan digest"):
        expanded_calibration.execute_expanded_run(
            ROOT,
            plan=plan,
            approved_plan_sha256="sha256:" + "0" * 64,
            output_directory=tmp_path / "expanded",
            transport=transport,  # type: ignore[arg-type]
        )

    assert transport.wire_requests_sent == 0


def test_expanded_run_stops_after_first_invalid_provider_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(_root: Path, *args: str) -> str:
        return "" if args == ("status", "--porcelain", "--untracked-files=no") else "revision-1"

    monkeypatch.setattr(expanded_calibration, "_git", git)
    plan = expanded_calibration.build_plan(ROOT)
    transport = InvalidOutputTransport()

    result = expanded_calibration.execute_expanded_run(
        ROOT,
        plan=plan,
        approved_plan_sha256=expanded_calibration.plan_digest(plan),
        output_directory=tmp_path / "expanded",
        transport=transport,  # type: ignore[arg-type]
    )

    assert result["provider_calls_made"] == 1
    assert result["model_attempt_reservations"] == 1
    assert result["attempted_tasks"] == 1
    assert result["successful_tasks"] == 0
    assert result["classifications"] == {"invalid_output": 1}
    assert result["episode_results"][0]["environment_actions"] == 0
    assert (tmp_path / "expanded/summary.json").is_file()
