"""Prepare the two no-cost demo evaluations; human approval actions remain manual."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.policy import is_verified_clean_revision

FIXTURES = (
    ("1", "day3-replay-baseline-v1"),
    ("2", "day3-replay-revised-v2"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    revision = os.environ.get("PIXELGYM_CODE_REVISION", "unknown-dirty")
    if not is_verified_clean_revision(revision):
        raise SystemExit(
            "set PIXELGYM_CODE_REVISION to the exact 40-character lowercase clean Git commit "
            "before preparing candidates"
        )
    control = ControlStore(
        args.database,
        reviewer_identity=os.environ.get("PIXELGYM_REVIEWER_ID", "local-reviewer"),
    )
    for prompt_version, model in FIXTURES:
        request = {
            "dataset": "day3-frozen-v1",
            "prompt_version": prompt_version,
            "model": model,
            "condition": "raw",
            "maximum_calls": "100",
            "price_catalog": "pixelgym-demo-prices-v1",
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
    print("Both scripted candidates are prepared. Approval, deployment, and rollback remain manual.")


if __name__ == "__main__":
    main()
