"""Describe stored calibration coverage and costs without issuing provider requests."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest

DIRECTORY = Path(__file__).parent


def analyze(summary: dict, plan: dict) -> dict:
    rows = summary["conditions"]
    assert len(rows) == len(plan["jobs"]) == 100
    for row, job in zip(rows, plan["jobs"], strict=True):
        assert all(row[key] == value for key, value in job.items())
    pairs: Counter[str] = Counter()
    for seed in sorted({row["seed"] for row in rows}):
        pair = {row["mode"]: row for row in rows if row["seed"] == seed}
        assert set(pair) == {"history", "stateless"}
        if any(
            row["classification"] in ("not_run", "budget_stop", "interrupted_episode")
            for row in pair.values()
        ):
            pairs["incomplete"] += 1
            continue
        history, stateless = (pair[mode]["success"] for mode in ("history", "stateless"))
        pairs[
            "both_success"
            if history and stateless
            else "history_only"
            if history
            else "stateless_only"
            if stateless
            else "neither_success"
        ] += 1
    spend = summary["aggregate_spend"]
    accounted = Decimal(spend["budget_accounted_spend_usd"])
    reservation = Decimal(plan["per_request_reservation_usd"])
    ceiling = Decimal(plan["aggregate_ceiling_usd"])
    assert accounted <= ceiling
    return {
        "schema_version": "pixelgym-d58-full-memory-descriptive-analysis-v1",
        "summary_digest": content_digest(summary),
        "execution_plan_digest": plan["execution_plan_digest"],
        "paired_terminal_outcomes": {
            key: pairs[key]
            for key in (
                "both_success",
                "history_only",
                "stateless_only",
                "neither_success",
                "incomplete",
            )
        },
        "started_final_stages": {
            mode: dict(
                sorted(
                    Counter(
                        str(row["final_stage_index"])
                        for row in rows
                        if row["mode"] == mode and row["model_attempts"]
                    ).items()
                )
            )
            for mode in ("history", "stateless")
        },
        "new_phase_known_spend_usd": str(
            Decimal(spend["spent_usd"]) - Decimal(summary["prior_pilot_spend_usd"])
        ),
        "new_phase_wire_requests": spend["wire_requests_sent"]
        - plan["prior_pilot_spend"]["wire_requests_sent"],
        "aggregate_known_spend_usd": spend["spent_usd"],
        "aggregate_unknown_hold_usd": spend["unknown_reservation_usd"],
        "aggregate_accounted_spend_usd": str(accounted),
        "remaining_unreserved_ceiling_usd": str(ceiling - accounted),
        "next_request_bound_usd": str(reservation),
        "next_request_fits": accounted + reservation <= ceiling,
        "inferential_test": None,
        "confirmatory_power_estimate": None,
        "interpretation": "Descriptive calibration only. Incomplete pairs remain explicit; a budget-stopped campaign cannot establish complete calibration rates or confirmatory power. Infrastructure failures remain failed outcomes in completed pairs. Do not pool these terminal results with the scripted-prefix pilot.",
        "provider_calls": 0,
    }


if __name__ == "__main__":
    summary = json.loads((DIRECTORY / "summary.json").read_text())
    plan = json.loads((DIRECTORY / "execution-plan.json").read_text())
    (DIRECTORY / "analysis.json").write_text(
        json.dumps(analyze(summary, plan), sort_keys=True, indent=2) + "\n"
    )
