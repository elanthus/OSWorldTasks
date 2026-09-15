"""Publish response-free corrections without replacing frozen calibration bundles."""

from __future__ import annotations

import argparse
import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/grounding-v5-d58-review-corrections"
PILOT = ROOT / "artifacts/grounding-v5-d58-calibration-pilot"
ADMISSION = ROOT / "artifacts/grounding-v5-d58-design/memory-repair/admission.json"


def corrected_pilot_analysis(summary: dict[str, Any]) -> dict[str, Any]:
    legacy = importlib.import_module("artifacts.grounding-v5-d58-calibration-pilot.analyze")
    value: dict[str, Any] = legacy.analyze(summary)
    del value["provider_calls"]
    value.update(
        schema_version="pixelgym-d58-memory-pilot-descriptive-analysis-v2",
        provider_control_requests=summary["provider_control_requests"],
        wire_requests_sent=summary["spend"]["wire_requests_sent"],
        provider_calls_by_analysis=0,
    )
    return value


def corrected_admission_summary(value: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = deepcopy(value["summary"])
    base_seeds = {row["base_seed"] for row in value["counterfactuals"]}
    summary["base_task_count"] = sum(row["seed"] in base_seeds for row in value["tasks"])
    summary["base_choice_count"] = sum(summary["memory_target_positions"].values())
    for rule, rows in value["baselines"].items():
        summary["baselines"][rule]["task_count"] = len(rows)
        summary["baselines"][rule]["choice_count"] = sum(len(row["choices"]) for row in rows)
    return summary


def payloads() -> dict[str, dict[str, Any]]:
    summary = json.loads((PILOT / "summary.json").read_text())
    admission = json.loads(ADMISSION.read_text())
    inputs = (
        PILOT / "summary.json",
        PILOT / "analysis.json",
        PILOT / "analyze.py",
        ADMISSION,
        Path(__file__).resolve(),
        ROOT / "scripts/verify_grounding_v5_full_calibration.py",
    )
    return {
        "pilot-analysis-v2.json": corrected_pilot_analysis(summary),
        "admission-summary-v2.json": {
            "schema_version": "pixelgym-d58-admission-summary-correction-v1",
            "original_admission_digest": content_digest(admission),
            "summary": corrected_admission_summary(admission),
            "new_task_executions": 0,
            "provider_calls": 0,
        },
        "correction.json": {
            "schema_version": "pixelgym-d58-review-corrections-v1",
            "source_file_digests": {
                str(path.relative_to(ROOT)): "sha256:" + sha256_bytes(path.read_bytes())
                for path in inputs
            },
            "corrections": [
                "The historical provider_calls=0 describes the analysis process, not pilot execution. The corrected analysis separately reports provider_control_requests, wire_requests_sent, and provider_calls_by_analysis.",
                "Development summary denominators are derived from stored task and choice rows. The active admission builder now records these counts for report rendering.",
                "Use scripts.verify_grounding_v5_full_calibration for explicit verification checks that remain active with Python -O. The old verifier remains reproduction-only inside its hash-bound historical bundle.",
            ],
            "original_artifacts_replaced": False,
            "provider_calls": 0,
            "new_spend_usd": "0",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    values = {
        name: json.dumps(value, sort_keys=True, indent=2) + "\n"
        for name, value in payloads().items()
    }
    if args.verify:
        for name, value in values.items():
            if (OUTPUT / name).read_text() != value:
                raise ValueError(f"correction evidence mismatch: {name}")
        print("Verified corrections from stored evidence; provider calls: 0")
    else:
        OUTPUT.mkdir()
        for name, value in values.items():
            (OUTPUT / name).write_text(value)


if __name__ == "__main__":
    main()
