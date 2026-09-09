from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pixelgym.grounding.v5.contracts import content_digest
from scripts.record_grounding_v5_d56_retained_development_runs import (
    PlanSpec,
    RegistryError,
    RunSpec,
    build_registry,
    classification_counts,
    record_run,
    write_registry,
)


def _seed(root: Path, run_id: str, *, journal_bytes: bytes = b"sqlite-bytes") -> dict[str, str]:
    plan = {
        "purpose": "one development-only episode",
        "code_revision": "a" * 40,
        "policy": {
            "slot": "C-example-stateful",
            "policy_manifest_digest": "sha256:" + "b" * 64,
            "provider": {"name": "openrouter", "upstream_provider": "Example"},
        },
        "journal_path": f"/Users/operator/checkout/artifacts/grounding-v5-d56-{run_id}-run/attempts.sqlite",
        "summary_path": f"/Users/operator/checkout/artifacts/grounding-v5-d56-{run_id}-run/summary.json",
    }
    summary = {
        "approved_plan_sha256": content_digest(plan),
        "code_revision": "a" * 40,
        "provider_calls_made": 3,
        "assigned_policy_task_pairs": 1,
        "actual_aggregate_spend_usd": "2.50",
        "prior_aggregate_spend_usd": "2.00",
        "maximum_aggregate_spend_usd": "10.00",
        "journal_integrity": {"event_chain_digest": "sha256:" + "c" * 64},
        "episode_result": {
            "classification": "infrastructure_failure",
            "slot": "C-example-stateful",
        },
        "journal_path": plan["journal_path"],
        "summary_path": plan["summary_path"],
    }
    plan_path = root / f"artifacts/grounding-v5-d56-{run_id}-plan.json"
    run_dir = root / f"artifacts/grounding-v5-d56-{run_id}-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "attempts.sqlite").write_bytes(journal_bytes)
    return {"plan": plan_path.read_text(encoding="utf-8")}


def test_registry_carries_digests_but_never_operator_paths(tmp_path: Path) -> None:
    _seed(tmp_path, "example-trial")
    spec = RunSpec("example-trial", ("development_only_not_calibration_evidence",))

    registry = build_registry(tmp_path, run_specs=(spec,), plan_specs=())
    path = write_registry(tmp_path, registry)

    text = path.read_text(encoding="utf-8")
    assert "/Users/" not in text and "operator/checkout" not in text
    run = registry["runs"][0]
    assert run["classification_counts"] == {
        "attempted": 1,
        "infrastructure_failure": 1,
        "invalid_output": 0,
        "policy_violation": 0,
        "request_failure": 0,
        "success": 0,
        "truncation": 0,
    }
    digests = run["authoritative_digests"]
    assert (
        digests["attempt_journal_file_sha256"]
        == "sha256:" + hashlib.sha256(b"sqlite-bytes").hexdigest()
    )
    assert digests["attempt_journal_size_bytes"] == len(b"sqlite-bytes")
    assert (
        digests["approved_plan_content_sha256"]
        == json.loads(
            (tmp_path / "artifacts/grounding-v5-d56-example-trial-run/summary.json").read_text()
        )["approved_plan_sha256"]
    )
    assert run["spend"]["incremental_spend_usd"] == "0.50"
    assert [row["path"] for row in run["excluded_artifacts"]] == [
        "artifacts/grounding-v5-d56-example-trial-plan.json",
        "artifacts/grounding-v5-d56-example-trial-run/summary.json",
        "artifacts/grounding-v5-d56-example-trial-run/attempts.sqlite",
    ]
    with pytest.raises(FileExistsError):
        write_registry(tmp_path, registry)


def test_plan_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "example-trial")
    summary_path = tmp_path / "artifacts/grounding-v5-d56-example-trial-run/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["approved_plan_sha256"] = "sha256:" + "0" * 64
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(RegistryError, match="does not match"):
        record_run(
            tmp_path, RunSpec("example-trial", ("development_only_not_calibration_evidence",))
        )


def test_retained_plan_must_match_its_committed_summary(tmp_path: Path) -> None:
    _seed(tmp_path, "example-trial")
    plan_spec = PlanSpec(
        Path("artifacts/grounding-v5-d56-example-trial-plan.json"),
        Path("artifacts/grounding-v5-d56-example-trial-run/summary.json"),
        "executed",
    )
    registry = build_registry(tmp_path, run_specs=(), plan_specs=(plan_spec,))
    assert registry["plans"][0]["committed_summary_records_this_content_digest"] is True

    plan_path = tmp_path / plan_spec.plan_path
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["purpose"] = "edited after approval"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(RegistryError, match="does not record this plan"):
        build_registry(tmp_path, run_specs=(), plan_specs=(plan_spec,))


def test_unknown_classification_is_rejected() -> None:
    with pytest.raises(RegistryError, match="unrecognized classification"):
        classification_counts({"episode_results": [{"classification": "mystery"}]})
    assert (
        classification_counts({"episode_results": [{"classification": "step_limit_truncation"}]})[
            "truncation"
        ]
        == 1
    )
