"""Prepare the two demo candidates plus a distinct no-cost rollback seed."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pixelgym.platform.control_store import ControlStore

FIXTURES = (
    ("1", "day3-replay-baseline-v1", "blocked-candidate-a"),
    ("2", "day3-replay-revised-rollback-seed-v1", "rollback-seed"),
    ("2", "day3-replay-revised-v2", "eligible-candidate-b"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    control = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    for prompt_version, model, lifecycle_role in FIXTURES:
        request = {
            "dataset": "day3-frozen-v1",
            "prompt_version": prompt_version,
            "model": model,
            "condition": "raw",
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
            "lifecycle_role": lifecycle_role,
        }
        submission_id = control.submit(request)
        command = [
            sys.executable,
            str(root / "flows/grounding_evaluation_flow.py"),
            "run",
            "--submission-id",
            submission_id,
            "--prompt-version",
            prompt_version,
            "--model",
            model,
            "--maximum-calls",
            "100",
            "--max-workers",
            "1",
        ]
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode:
            control.mark_submission(submission_id, "Failed")
            raise SystemExit(completed.returncode)
    print(
        "Both scripted candidates and the rollback seed are prepared. "
        "Approval, deployment, and rollback remain manual."
    )


if __name__ == "__main__":
    main()
