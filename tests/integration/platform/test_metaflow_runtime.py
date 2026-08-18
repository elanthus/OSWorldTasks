"""Production-FlowSpec acceptance tests using Metaflow's real local runtime."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from pixelgym.platform.control_store import ControlStore
from pixelgym.platform.fingerprints import canonical_json_bytes
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.mlflow_tracking import MlflowTracking
from pixelgym.platform.runtime_fixture import provider_ledger_snapshot
from pixelgym.platform.source_provenance import (
    SOURCE_PROVENANCE_SCHEMA_VERSION,
    SourceProvenance,
    source_tree_sha256,
)

pytest.importorskip("mlflow")


pytestmark = pytest.mark.platform_integration

REQUEST = {
    "dataset": "day3-frozen-v1",
    "prompt_version": "2",
    "model": "day3-replay-revised-v2",
    "condition": "raw",
    "maximum_calls": "100",
    "price_catalog": "pixelgym-demo-prices-v1",
}
BOUNDARIES = (
    "run_linked",
    "provider_response_received",
    "raw_responses_persisted",
    "evidence_persisted",
    "mlflow_finalized",
    "candidate_registered",
)


@dataclass(frozen=True)
class RuntimeResult:
    root: Path
    submission_id: str
    origin_run_id: str
    output: str


def _control(root: Path) -> ControlStore:
    return ControlStore(root / "control.db", reviewer_identity="local-reviewer")


def _prepare(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    control = _control(root)
    control.migrate()
    return control.submit(REQUEST)


def _environment(repository_root: Path, root: Path, failpoint: str | None) -> dict[str, str]:
    provenance_path = root / "source-provenance.json"
    provenance_path.write_text(
        json.dumps(
            SourceProvenance(
                SOURCE_PROVENANCE_SCHEMA_VERSION,
                "a" * 40,
                source_tree_sha256(repository_root),
                "clean",
                "git-build-inputs-v1",
            ).to_dict()
        )
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELGYM_REPOSITORY_ROOT": str(repository_root),
            "PIXELGYM_IMMUTABLE_ROOT": str(root / "immutable"),
            "PIXELGYM_CONTROL_DB": str(root / "control.db"),
            "PIXELGYM_SOURCE_PROVENANCE_PATH": str(provenance_path),
            "MLFLOW_TRACKING_URI": f"sqlite:///{root / 'mlflow.db'}",
            "PIXELGYM_ENABLE_TEST_HOOKS": "1",
            "PIXELGYM_TEST_PROVIDER_LEDGER": str(root / "provider.db"),
            "PIXELGYM_TEST_CONCURRENCY_BARRIER": "2",
            "PIXELGYM_TEST_STATE_ROOT": str(root / "failpoints"),
            "METAFLOW_USER": "pixelgym-runtime-test",
        }
    )
    if failpoint is None:
        environment.pop("PIXELGYM_TEST_FAIL_ONCE", None)
    else:
        environment["PIXELGYM_TEST_FAIL_ONCE"] = failpoint
    return environment


def _invoke(
    repository_root: Path,
    root: Path,
    arguments: list[str],
    *,
    failpoint: str | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repository_root / "flows/grounding_evaluation_flow.py"),
            *arguments,
        ],
        cwd=root,
        env=_environment(repository_root, root, failpoint),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )


def _run_uninterrupted(repository_root: Path, root: Path) -> RuntimeResult:
    submission_id = _prepare(root)
    run_id_file = root / "origin-run-id"
    completed = _invoke(
        repository_root,
        root,
        [
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
            str(run_id_file),
        ],
        failpoint=None,
    )
    assert completed.returncode == 0, completed.stdout
    assert "Foreach yields 4 child steps" in completed.stdout
    return RuntimeResult(root, submission_id, run_id_file.read_text().strip(), completed.stdout)


def _run_failed_then_resume(
    repository_root: Path,
    root: Path,
    failpoint: str,
) -> RuntimeResult:
    submission_id = _prepare(root)
    origin_file = root / "origin-run-id"
    failed = _invoke(
        repository_root,
        root,
        [
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
            str(origin_file),
        ],
        failpoint=failpoint,
    )
    assert failed.returncode != 0, failed.stdout
    assert f"injected one-shot failure after {failpoint}" in failed.stdout
    origin_run_id = origin_file.read_text().strip()

    control = _control(root)
    candidates_before_resume = control.list_candidates()
    if failpoint == "candidate_registered":
        assert len(candidates_before_resume) == 1
        assert control.list_submissions()[0]["status"] == "Complete"
    else:
        assert candidates_before_resume == []
        assert control.list_submissions()[0]["status"] == "Failed"

    resumed = _invoke(
        repository_root,
        root,
        [
            "resume",
            "--origin-run-id",
            origin_run_id,
            "--max-workers",
            "2",
            "--run-id-file",
            str(root / "resume-run-id"),
        ],
        failpoint=failpoint,
    )
    assert resumed.returncode == 0, resumed.stdout
    assert "Cloning" in resumed.stdout
    assert "Done!" in resumed.stdout
    return RuntimeResult(root, submission_id, origin_run_id, failed.stdout + resumed.stdout)


def _final_evidence(result: RuntimeResult) -> tuple[bytes, bytes, bytes, bytes]:
    store = LocalImmutableStore(result.root / "immutable")
    predictions_ref = store.get_reference(
        f"runs/{result.submission_id}/predictions.jsonl"
    )
    gate_ref = store.get_reference(f"runs/{result.submission_id}/gate-report.json")
    assert predictions_ref is not None and gate_ref is not None
    predictions = store.get_verified(predictions_ref)
    gate = json.loads(store.get_verified(gate_ref))

    control = _control(result.root)
    candidates = control.list_candidates()
    assert len(candidates) == 1
    candidate = candidates[0]
    policy_ref = store.get_reference(
        f"policies/{candidate.policy.policy_id.removeprefix('sha256:')}.json"
    )
    assert policy_ref is not None
    policy = store.get_verified(policy_ref)

    gate["run_id"] = "<run-id>"
    candidate_gate = dict(candidate.gate_report)
    candidate_gate["run_id"] = "<run-id>"
    artifact_values = [artifact.to_dict() for artifact in candidate.artifacts]
    for artifact in artifact_values:
        run_bound_suffix = next(
            (
                suffix
                for suffix in (
                    "gate-report.json",
                    "summary.json",
                    "run-manifest.json",
                )
                if artifact["logical_key"].endswith(f"/{suffix}")
            ),
            None,
        )
        if run_bound_suffix is not None:
            placeholder = f"<run-bound-{run_bound_suffix}-sha256>"
            artifact["sha256"] = placeholder
            artifact["version_id"] = placeholder
    canonical_candidate = canonical_json_bytes(
        {
            "candidate_id": candidate.candidate_id,
            "source_run_id": "<run-id>",
            "policy": candidate.policy.to_dict(),
            "gate_report": candidate_gate,
            "artifacts": artifact_values,
            "state": candidate.state.value,
        }
    )
    return predictions, canonical_json_bytes(gate), policy, canonical_candidate


@pytest.fixture(scope="module")
def uninterrupted_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> RuntimeResult:
    repository_root = Path(__file__).parents[3]
    return _run_uninterrupted(
        repository_root,
        tmp_path_factory.mktemp("metaflow-uninterrupted"),
    )


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_metaflow_resume_at_each_side_effect_boundary(
    boundary: str,
    tmp_path: Path,
    uninterrupted_runtime: RuntimeResult,
) -> None:
    repository_root = Path(__file__).parents[3]
    resumed = _run_failed_then_resume(repository_root, tmp_path, boundary)

    evidence = _final_evidence(resumed)
    assert len(evidence[0].splitlines()) == 100
    assert len(
        {
            (record["example_id"], record["condition"])
            for record in map(json.loads, evidence[0].splitlines())
        }
    ) == 100
    assert evidence == _final_evidence(uninterrupted_runtime)

    ledger = provider_ledger_snapshot(tmp_path / "provider.db")
    assert ledger["unique_request_ids"] == 100
    assert ledger["billable_calls"] == 100
    assert ledger["active"] == 0
    assert ledger["max_active"] == 2
    if boundary == "provider_response_received":
        assert ledger["attempts"] == 101
        assert ledger["cache_hits"] == 1
    else:
        assert ledger["attempts"] == 100
        assert ledger["cache_hits"] == 0

    control = _control(tmp_path)
    submission = control.list_submissions()[0]
    candidate = control.list_candidates()[0]
    assert submission["status"] == "Complete"
    assert submission["metaflow_pathspec"] == (
        f"GroundingEvaluationFlow/{resumed.origin_run_id}"
    )
    tracking = MlflowTracking(f"sqlite:///{tmp_path / 'mlflow.db'}")
    assert tracking.client.get_run(candidate.source_run_id).info.status == "FINISHED"


def test_uninterrupted_runtime_enforces_worker_and_call_caps(
    uninterrupted_runtime: RuntimeResult,
) -> None:
    ledger = provider_ledger_snapshot(uninterrupted_runtime.root / "provider.db")
    assert ledger == {
        "attempts": 100,
        "unique_request_ids": 100,
        "cache_hits": 0,
        "active": 0,
        "max_active": 2,
        "billable_calls": 100,
    }
    assert len(_final_evidence(uninterrupted_runtime)[0].splitlines()) == 100


def test_metaflow_hard_kill_after_durable_evidence_resumes_without_duplicate_calls(
    tmp_path: Path,
    uninterrupted_runtime: RuntimeResult,
) -> None:
    """Kill the whole local process group; remote schedulers still need orphan reconciliation."""
    repository_root = Path(__file__).parents[3]
    submission_id = _prepare(tmp_path)
    origin_file = tmp_path / "origin-run-id"
    environment = _environment(repository_root, tmp_path, None)
    environment.update(
        {
            "PIXELGYM_TEST_PAUSE_ONCE": "evidence_persisted",
            "PIXELGYM_TEST_PAUSE_TIMEOUT_SECONDS": "90",
        }
    )
    log_path = tmp_path / "hard-kill-run.log"
    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                str(repository_root / "flows/grounding_evaluation_flow.py"),
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
                str(origin_file),
            ],
            cwd=tmp_path,
            env=environment,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        marker = tmp_path / "failpoints/evidence_persisted.paused"
        deadline = time.monotonic() + 90
        try:
            while time.monotonic() < deadline and not marker.exists():
                if process.poll() is not None:
                    log_file.flush()
                    pytest.fail(
                        f"flow exited before the hard-kill boundary:\n{log_path.read_text()}"
                    )
                time.sleep(0.05)
            assert marker.exists(), "flow did not reach the durable hard-kill boundary"
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)

    assert process.returncode == -signal.SIGKILL, log_path.read_text()
    origin_run_id = origin_file.read_text().strip()
    interrupted_control = _control(tmp_path)
    assert interrupted_control.list_candidates() == []
    assert interrupted_control.list_submissions()[0]["status"] == "Running"

    resumed = _invoke(
        repository_root,
        tmp_path,
        [
            "resume",
            "--origin-run-id",
            origin_run_id,
            "--max-workers",
            "2",
            "--run-id-file",
            str(tmp_path / "resume-run-id"),
        ],
        failpoint=None,
    )
    assert resumed.returncode == 0, resumed.stdout
    assert "Cloning" in resumed.stdout
    assert _final_evidence(RuntimeResult(tmp_path, submission_id, origin_run_id, "")) == (
        _final_evidence(uninterrupted_runtime)
    )
    assert provider_ledger_snapshot(tmp_path / "provider.db") == {
        "attempts": 100,
        "unique_request_ids": 100,
        "cache_hits": 0,
        "active": 0,
        "max_active": 2,
        "billable_calls": 100,
    }
