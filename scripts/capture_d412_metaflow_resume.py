#!/usr/bin/env python3
"""Capture structured Metaflow resume ledgers from the real local runtime."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_runtime_module(repository_root: Path) -> ModuleType:
    path = repository_root / "tests/integration/platform/test_metaflow_runtime.py"
    spec = importlib.util.spec_from_file_location("d412_metaflow_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runtime test helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(module: ModuleType, result: Any, baseline: tuple[bytes, ...]) -> dict[str, Any]:
    evidence = module._final_evidence(result)
    records = [json.loads(line) for line in evidence[0].splitlines()]
    control = module._control(result.root)
    candidate = control.list_candidates()[0]
    tracking = module.MlflowTracking(f"sqlite:///{result.root / 'mlflow.db'}")
    return {
        "normalized_evidence_equal": evidence == baseline,
        "prediction_count": len(records),
        "unique_example_conditions": len(
            {(row["example_id"], row["condition"]) for row in records}
        ),
        "provider_ledger": module.provider_ledger_snapshot(result.root / "provider.db"),
        "submission_status": control.list_submissions()[0]["status"],
        "candidate_count": len(control.list_candidates()),
        "mlflow_status": tracking.client.get_run(candidate.source_run_id).info.status,
    }


def _hard_kill(
    module: ModuleType,
    repository_root: Path,
    root: Path,
    baseline: tuple[bytes, ...],
) -> dict[str, Any]:
    submission_id = module._prepare(root)
    origin_file = root / "origin-run-id"
    environment = module._environment(repository_root, root, None)
    environment.update(
        {
            "PIXELGYM_TEST_PAUSE_ONCE": "evidence_persisted",
            "PIXELGYM_TEST_PAUSE_TIMEOUT_SECONDS": "90",
        }
    )
    log_path = root / "hard-kill-run.log"
    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                os.fspath(repository_root / "flows/grounding_evaluation_flow.py"),
                "run",
                "--submission-id",
                submission_id,
                "--prompt-version",
                "2",
                "--model",
                "day3-replay-revised-v2",
                "--maximum-calls",
                "100",
                "--shard-size",
                "25",
                "--max-workers",
                "2",
                "--run-id-file",
                os.fspath(origin_file),
            ],
            cwd=root,
            env=environment,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        marker = root / "failpoints/evidence_persisted.paused"
        deadline = time.monotonic() + 90
        try:
            while time.monotonic() < deadline and not marker.exists():
                if process.poll() is not None:
                    log_file.flush()
                    raise RuntimeError(
                        "flow exited before hard-kill boundary:\n" + log_path.read_text()
                    )
                time.sleep(0.05)
            if not marker.exists():
                raise RuntimeError("flow did not reach the durable hard-kill boundary")
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    if process.returncode != -signal.SIGKILL:
        raise RuntimeError(f"unexpected hard-kill return code: {process.returncode}")

    origin_run_id = origin_file.read_text().strip()
    resumed = module._invoke(
        repository_root,
        root,
        [
            "resume",
            "--origin-run-id",
            origin_run_id,
            "--max-workers",
            "2",
            "--run-id-file",
            os.fspath(root / "resume-run-id"),
        ],
        failpoint=None,
    )
    if resumed.returncode != 0:
        raise RuntimeError(resumed.stdout)
    result = module.RuntimeResult(root, submission_id, origin_run_id, resumed.stdout)
    snapshot = _snapshot(module, result, baseline)
    snapshot["killed_with_sigkill"] = True
    return snapshot


def capture(repository_root: Path) -> dict[str, Any]:
    module = _load_runtime_module(repository_root)
    with tempfile.TemporaryDirectory(prefix="pixelgym-d412-resume-") as temporary:
        root = Path(temporary)
        uninterrupted = module._run_uninterrupted(repository_root, root / "uninterrupted")
        baseline = module._final_evidence(uninterrupted)
        results = {
            "uninterrupted": _snapshot(module, uninterrupted, baseline),
            "boundaries": {},
        }
        for boundary in module.BOUNDARIES:
            resumed = module._run_failed_then_resume(
                repository_root, root / f"boundary-{boundary}", boundary
            )
            results["boundaries"][boundary] = _snapshot(module, resumed, baseline)
        results["hard_kill"] = _hard_kill(module, repository_root, root / "hard-kill", baseline)
    return {
        "schema_version": "pixelgym-d412-metaflow-resume-ledgers-v1",
        **results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(capture(args.repository_root.resolve()), sort_keys=True))


if __name__ == "__main__":
    main()
