#!/usr/bin/env python3
"""Record digest-only entries for retained D5.6 development runs without provider calls.

The six runs and two plans listed here were recorded at runner revisions that wrote absolute
operator paths into the digest-bound ``journal_path`` and ``summary_path`` fields. Committing
those files would publish host identity; rewriting them would break the approved-plan content
digests that later summaries cite. This script reads the sealed local originals, verifies each
plan's content digest against its summary, and writes a registry that carries only digests,
counts, identities, and ledger values. ``scripts/publish_grounding_v5_d56_completed_calibrations.py``
consumes that registry; the originals stay local and are declared ``must_not_commit``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import REDACTION_POLICY_VERSION, content_digest
from pixelgym.grounding.v5.evidence import validate_credential_free

REGISTRY_PATH = Path("artifacts/grounding-v5-d56-retained-development-runs.json")
REGISTRY_SCHEMA_VERSION = "pixelgym-agent-v5-d56-retained-development-runs-v1"
_LOCAL_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\Users\\\\)")
_COUNT_KEYS = (
    "infrastructure_failure",
    "invalid_output",
    "policy_violation",
    "request_failure",
    "success",
    "truncation",
)
_CLASS_MAP = {
    "success": "success",
    "success_termination": "success",
    "step_limit_truncation": "truncation",
    "infrastructure_failure": "infrastructure_failure",
    "unknown_outcome_infrastructure_failure": "infrastructure_failure",
    "invalid_output": "invalid_output",
    "policy_violation": "policy_violation",
    "request_failure": "request_failure",
}
PATH_REASON = "absolute operator paths in digest-bound fields"
JOURNAL_REASON = "restricted raw provider responses, screenshots, and private checkpoints"
RETENTION_REASON = (
    "The approved plan and run summary record absolute operator paths inside the digest-bound "
    "journal_path and summary_path fields (runner revisions before repository-relative paths). "
    "Committing them would publish host identity; rewriting them would break the approved-plan "
    "content digest that later summaries cite. The files stay sealed locally, unmodified."
)
VERIFICATION_LIMIT = (
    "Plan, summary, and attempt-journal digests are verifiable only by the owner from the "
    "sealed local copies; no file for these runs is committed."
)


class RegistryError(RuntimeError):
    """The sealed originals do not support a digest-only registry entry."""


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    failed_checks: tuple[str, ...]

    @property
    def plan_path(self) -> Path:
        return Path(f"artifacts/grounding-v5-d56-{self.run_id}-plan.json")

    @property
    def run_directory(self) -> Path:
        return Path(f"artifacts/grounding-v5-d56-{self.run_id}-run")


@dataclass(frozen=True)
class PlanSpec:
    plan_path: Path
    committed_run_summary: Path | None
    status: str


_PATHS = ("committable_credential_free_paths",)
RUN_SPECS: tuple[RunSpec, ...] = (
    RunSpec("bcd-calibration", ("completed_assigned_denominator", *_PATHS)),
    RunSpec("c-calibration", ("completed_assigned_denominator", *_PATHS)),
    RunSpec("c-normalized-trial", ("development_only_not_calibration_evidence", *_PATHS)),
    RunSpec("glm-json-object-smoke", ("development_only_not_calibration_evidence", *_PATHS)),
    RunSpec("glm-normalized-trial", ("development_only_not_calibration_evidence", *_PATHS)),
    RunSpec("glm-relaxed-trial", ("development_only_not_calibration_evidence", *_PATHS)),
)
# The withdrawn ``A-gemini-stateful-v2`` plan (``grounding-v5-d56-calibration-plan-v2.json``) is
# deliberately absent: README.md records that its evidence was removed rather than corrected and
# is not included in any published result. It stays sealed locally and ignored.
PLAN_SPECS: tuple[PlanSpec, ...] = (
    PlanSpec(
        Path("artifacts/grounding-v5-d56-calibration-plan.json"),
        Path("artifacts/grounding-v5-d56-calibration-run/summary.json"),
        "executed; its committed run summary records this plan's content digest",
    ),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected a JSON object: {path.name}")
    return value


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _excluded(path: Path, repository_root: Path, reason: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(repository_root).as_posix(),
        "sha256": _file_digest(path),
        "size_bytes": path.stat().st_size,
        "reason": reason,
    }


def classification_counts(summary: dict[str, Any]) -> dict[str, int]:
    episodes = summary.get("episode_results")
    if episodes is None:
        episodes = [summary["episode_result"]] if summary.get("episode_result") else []
    tally: Counter[str] = Counter()
    for episode in episodes:
        label = episode["classification"]
        _require(label in _CLASS_MAP, f"unrecognized classification: {label}")
        tally[_CLASS_MAP[label]] += 1
    return {"attempted": len(episodes), **{key: tally.get(key, 0) for key in _COUNT_KEYS}}


def _spend(summary: dict[str, Any]) -> dict[str, Any]:
    actual = summary.get("actual_aggregate_spend_usd")
    prior = summary.get("prior_aggregate_spend_usd")
    incremental = summary.get("calibration_incremental_spend_usd")
    if incremental is None and actual is not None and prior is not None:
        incremental = str(Decimal(actual) - Decimal(prior))
    return {
        "ledger": "aggregate campaign ledger as recorded by the run; not a per-run cap",
        "actual_aggregate_spend_usd": actual,
        "prior_aggregate_spend_usd": prior,
        "incremental_spend_usd": incremental,
        "maximum_aggregate_spend_usd": summary.get("maximum_aggregate_spend_usd"),
    }


def record_run(repository_root: Path, spec: RunSpec) -> dict[str, Any]:
    plan_path = repository_root / spec.plan_path
    summary_path = repository_root / spec.run_directory / "summary.json"
    journal_path = repository_root / spec.run_directory / "attempts.sqlite"
    plan, summary = _load_json(plan_path), _load_json(summary_path)
    _require(
        content_digest(plan) == summary["approved_plan_sha256"],
        f"{spec.run_id}: plan content digest does not match the summary's approved plan",
    )
    _require(journal_path.is_file(), f"{spec.run_id}: attempt journal is missing")
    policy = plan.get("policy") or plan["policies"][0]
    provider = policy["provider"]
    return {
        "run_id": spec.run_id,
        "slot": policy["slot"],
        "purpose": plan["purpose"],
        "classification_counts": classification_counts(summary),
        "assigned_tasks": summary.get("assigned_policy_task_pairs"),
        "failed_checks": list(spec.failed_checks),
        "code_revision": summary["code_revision"],
        "policy_manifest_digest": policy["policy_manifest_digest"],
        "provider_alias": "/".join(
            part for part in (provider["name"], provider.get("upstream_provider")) if part
        ),
        "provider_calls_made": summary.get("provider_calls_made"),
        "spend": _spend(summary),
        "authoritative_digests": {
            "approved_plan_content_sha256": summary["approved_plan_sha256"],
            "plan_file_sha256": _file_digest(plan_path),
            "summary_file_sha256": _file_digest(summary_path),
            "journal_event_chain_sha256": summary["journal_integrity"]["event_chain_digest"],
            "attempt_journal_file_sha256": _file_digest(journal_path),
            "attempt_journal_size_bytes": journal_path.stat().st_size,
        },
        "excluded_artifacts": [
            _excluded(plan_path, repository_root, PATH_REASON),
            _excluded(summary_path, repository_root, PATH_REASON),
            _excluded(journal_path, repository_root, JOURNAL_REASON),
        ],
    }


def record_plan(repository_root: Path, spec: PlanSpec) -> dict[str, Any]:
    plan_path = repository_root / spec.plan_path
    plan = _load_json(plan_path)
    digest = content_digest(plan)
    matches = None
    if spec.committed_run_summary is not None:
        summary = _load_json(repository_root / spec.committed_run_summary)
        matches = summary["approved_plan_sha256"] == digest
        _require(matches, f"{spec.plan_path.name}: committed summary does not record this plan")
    return {
        "plan_path": spec.plan_path.as_posix(),
        "purpose": plan["purpose"],
        "code_revision": plan.get("code_revision"),
        "status": spec.status,
        "committed_run_summary": (
            None if spec.committed_run_summary is None else spec.committed_run_summary.as_posix()
        ),
        "committed_summary_records_this_content_digest": matches,
        "authoritative_digests": {
            "plan_content_sha256": digest,
            "plan_file_sha256": _file_digest(plan_path),
        },
        "excluded_artifacts": [_excluded(plan_path, repository_root, PATH_REASON)],
    }


def build_registry(
    repository_root: Path,
    *,
    run_specs: Sequence[RunSpec] = RUN_SPECS,
    plan_specs: Sequence[PlanSpec] = PLAN_SPECS,
) -> dict[str, Any]:
    registry = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "purpose": (
            "digest-only record of D5.6 development runs and plans whose sealed originals stay "
            "local; consumed by the completed-calibrations publication"
        ),
        "provider_calls_made": 0,
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "retention_reason": RETENTION_REASON,
        "public_verification_limit": VERIFICATION_LIMIT,
        "runs": [record_run(repository_root, spec) for spec in run_specs],
        "plans": [record_plan(repository_root, spec) for spec in plan_specs],
    }
    validate_credential_free(registry)
    text = json.dumps(registry, indent=2, sort_keys=True)
    _require(not _LOCAL_PATH.search(text), "registry would contain an absolute operator path")
    return registry


def write_registry(repository_root: Path, registry: dict[str, Any]) -> Path:
    path = repository_root / REGISTRY_PATH
    if path.exists():
        raise FileExistsError(f"refusing to replace registry: {REGISTRY_PATH}")
    path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = args.repository_root.resolve()
    registry = build_registry(root)
    path = write_registry(root, registry)
    print(
        json.dumps(
            {
                "registry": path.relative_to(root).as_posix(),
                "registry_file_sha256": _file_digest(path),
                "runs": [run["run_id"] for run in registry["runs"]],
                "plans": [plan["plan_path"] for plan in registry["plans"]],
                "provider_calls_made": 0,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
