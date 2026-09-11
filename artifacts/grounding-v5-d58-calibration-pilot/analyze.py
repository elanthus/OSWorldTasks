"""Generate descriptive paired results solely from the stored pilot summary."""

import json
from collections import Counter
from decimal import Decimal
from math import comb
from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest

DIRECTORY = Path(__file__).parent


def analyze(summary: dict) -> dict:
    rows = summary["conditions"]
    counts: Counter[str] = Counter()
    for seed in sorted({row["seed"] for row in rows}):
        pair = {row["mode"]: row for row in rows if row["seed"] == seed}
        if len(pair) != 2 or not all(row["model_attempted"] for row in pair.values()):
            counts["incomplete_pair"] += 1
            continue
        history, stateless = (
            pair[mode]["first_attempt_correct"] for mode in ("history", "stateless")
        )
        counts[
            "both_correct"
            if history and stateless
            else "history_only"
            if history
            else "stateless_only"
            if stateless
            else "neither_correct"
        ] += 1
    discordant = counts["history_only"] + counts["stateless_only"]
    smaller = min(counts["history_only"], counts["stateless_only"])
    p_value = min(1.0, 2 * sum(comb(discordant, i) for i in range(smaller + 1)) / 2**discordant)
    return {
        "schema_version": "pixelgym-d58-memory-pilot-descriptive-analysis-v1",
        "summary_digest": content_digest(summary),
        "execution_plan_digest": summary["execution_plan_digest"],
        "paired_counts": {
            key: counts[key]
            for key in (
                "both_correct",
                "history_only",
                "stateless_only",
                "neither_correct",
                "incomplete_pair",
            )
        },
        "exploratory_exact_mcnemar_two_sided_p": p_value if not counts["incomplete_pair"] else None,
        "inference_scope": "exploratory first-attempt diagnostic; not the confirmatory terminal-success hypothesis",
        "history_consumer_misses": sum(
            row["mode"] == "history" and row["model_attempted"] and not row["valid_consumer_choice"]
            for row in rows
        ),
        "remaining_aggregate_ceiling_usd": str(
            Decimal(summary["maximum_aggregate_spend_usd"])
            - Decimal(summary["spend"]["budget_accounted_spend_usd"])
        ),
        "decision": "retain the frozen generator and policies; require end-to-end calibration before final evaluation approval",
        "provider_calls": 0,
    }


if __name__ == "__main__":
    summary = json.loads((DIRECTORY / "summary.json").read_text())
    (DIRECTORY / "analysis.json").write_text(
        json.dumps(analyze(summary), sort_keys=True, indent=2) + "\n"
    )
