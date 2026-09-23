"""Build or verify the response-free D5.9 Haiku owner-selection record."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "artifacts/grounding-v5-d58-haiku-successor/analysis.json"
OUTPUT = ROOT / "artifacts/grounding-v5-d59-haiku-selection"
SELECTION = OUTPUT / "owner-selection.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def build_selection() -> dict[str, Any]:
    """Derive the exact owner selection from the checked-in Haiku analysis."""
    analysis = _read(ANALYSIS)
    if analysis["schema_version"] != "pixelgym-d58-haiku-successor-analysis-v1":
        raise ValueError("unexpected Haiku analysis schema")
    if analysis["provider_calls_made"] != 0:
        raise ValueError("selection input must be response-free")
    candidate = analysis["carry_forward_candidate"]
    if candidate["independent_pairs"] != 168:
        raise ValueError("unexpected independent-pair candidate")
    if candidate["episodes_per_arm_with_robustness_twins"] != 192:
        raise ValueError("unexpected episode candidate")
    if not candidate["meets_power_at_observed_discordance"]:
        raise ValueError("candidate misses observed-discordance power target")
    if not candidate["meets_power_at_upper_sensitivity"]:
        raise ValueError("candidate misses upper-sensitivity power target")
    power = next(
        row["power"]
        for row in analysis["options"]
        if row["independent_pairs"] == candidate["independent_pairs"]
    )
    calibration = analysis["calibration"]
    sandbox = analysis["execution_readiness"]["os_sandbox_applied"]
    if sandbox != {"history": False, "stateless": False}:
        raise ValueError("selection expects the disclosed unsandboxed calibration boundary")
    return {
        "schema_version": "pixelgym-d59-haiku-owner-selection-v1",
        "recorded_at": "2026-09-23",
        "owner_statement": "yes, select haiku",
        "decision": (
            "Select the exact Haiku screenshot-history and stateless-current-frame policies "
            "and carry forward 168 independent representatives, or 192 episodes per arm "
            "including 24 robustness twins."
        ),
        "selection_status": "selected_for_versioned_d59_freeze_successor",
        "source": {
            "analysis_path": str(ANALYSIS.relative_to(ROOT)),
            "analysis_sha256": _digest(ANALYSIS),
            "analysis_schema_version": analysis["schema_version"],
            "analysis_merge_revision": "0b2567f1c7d90f2e3cbf08c409d93a355444b957",
        },
        "selected_pair": {
            "model": calibration["model"],
            "provider": calibration["provider"],
            "cli_version": calibration["cli_version"],
            "history_policy_id": calibration["history_policy_id"],
            "stateless_policy_id": calibration["stateless_policy_id"],
        },
        "selected_design": {
            "independent_representatives": candidate["independent_pairs"],
            "episodes_per_arm": candidate["episodes_per_arm_with_robustness_twins"],
            "robustness_twins_per_arm": (
                candidate["episodes_per_arm_with_robustness_twins"]
                - candidate["independent_pairs"]
            ),
            "power_at_observed_discordance": power["observed_discordance"],
            "power_at_upper_sensitivity": power["upper_sensitivity"],
            "minimum_relevant_absolute_difference": analysis[
                "minimum_relevant_absolute_difference"
            ],
            "target_power": analysis["power_target"],
            "alpha": analysis["alpha"],
        },
        "supersession": {
            "supersedes_gemini_candidate_policy_selection": True,
            "historical_gemini_evidence_and_freeze_preserved": True,
            "does_not_reuse_gemini_execution_approval": True,
        },
        "execution_boundary": {
            "execution_enabled": False,
            "paid_or_subscription_execution_authorized": False,
            "approved_model_attempt_cap": 0,
            "approved_provider_wire_request_cap": 0,
            "runtime_window_approved": False,
            "aggregate_planning_cap_usd": None,
            "confirmatory_tasks_generated": 0,
            "provider_calls_made": 0,
            "os_sandbox_applied": sandbox,
            "blockers": [
                "Apply and verify OS-level sandbox enforcement for both Claude Code CLI policies.",
                "Create a versioned D5.9 freeze binding admitted tasks, source and runtime digests, caps, runtime window, and subscription boundary.",
                "Obtain separate exact execution approval before any provider call.",
            ],
        },
        "milestone_verdict_declared": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    encoded = json.dumps(build_selection(), indent=2, sort_keys=True) + "\n"
    if args.verify:
        if SELECTION.read_text(encoding="utf-8") != encoded:
            raise ValueError("Haiku owner-selection record differs from stored evidence")
        print("Verified D5.9 Haiku owner selection; provider calls: 0")
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    SELECTION.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
