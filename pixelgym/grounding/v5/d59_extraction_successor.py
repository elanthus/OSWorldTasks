"""Separate, no-call parser-sensitivity candidate beside the completed replication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import d59_replication as replication
from pixelgym.grounding.v5.action_extraction import ADAPTER_VERSION, extraction_manifest
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest
from pixelgym.grounding.v5.d59_haiku_retry_successor import _binding, _sha256_at_revision

SCHEMA_VERSION = "pixelgym-agent-v5-d59-action-extraction-plan-v1"
OUTPUT_DIRECTORY = "artifacts/grounding-v5-d59-action-extraction-successor"
ORIGINAL_PLAN_PATH = "artifacts/grounding-v5-d59-replication/execution-plan.json"
ORIGINAL_PLAN_DIGEST = "sha256:ce6776def9066d2c3b4a1b2701b25567dc182364dfa3cf694b4044d865d59159"
SOURCE_FILES = (
    *replication.SOURCE_FILES,
    "pixelgym/grounding/v5/action_extraction.py",
    "pixelgym/grounding/v5/d59_extraction_successor.py",
    "pixelgym/grounding/v5/cli_memory_calibration.py",
    "pixelgym/grounding/v5/runner.py",
    "pixelgym/actions.py",
    "scripts/prepare_grounding_v5_d59_extraction.py",
)


def live_manifests(
    root: Path, *, source_revision: str, identity: claude.ClaudeRuntimeIdentity
) -> dict[str, PolicyManifest]:
    return {
        mode: extraction_manifest(root, manifest)
        for mode, manifest in replication.live_manifests(
            root, source_revision=source_revision, identity=identity
        ).items()
    }


def execution_plan(
    root: Path, *, source_revision: str, runtime_identity: claude.ClaudeRuntimeIdentity
) -> dict[str, Any]:
    original = json.loads((root / ORIGINAL_PLAN_PATH).read_text())
    if (
        original["execution_plan_digest"] != ORIGINAL_PLAN_DIGEST
        or content_digest({k: v for k, v in original.items() if k != "execution_plan_digest"})
        != ORIGINAL_PLAN_DIGEST
    ):
        raise ValueError("original replication freeze changed")
    if runtime_identity.to_dict() != original["runtime_identity"]:
        raise ValueError("successor must retain the exact runtime identity")
    plan = replication.execution_plan(
        root,
        source_revision=source_revision,
        runtime_identity=runtime_identity,
        stop_rule_options=replication.stop_rule_options_for_plan(original),
    )
    source = {path: _sha256_at_revision(root, source_revision, path) for path in SOURCE_FILES}
    for path, digest in source.items():
        if "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"execution source differs from revision: {path}")
    plan.pop("execution_plan_digest")
    plan.update(
        schema_version=SCHEMA_VERSION,
        status="prepared_successor_awaiting_owner_freeze_and_execution_approval",
        owner_direction={
            "statements": [
                {
                    "recorded_at": "2026-10-22",
                    "statement": (
                        "Proceed with the offline diagnostic and narrow extraction adapter. "
                        "Keep identity and sandbox checks strict, add regression tests, and "
                        "prepare a successor freeze for my approval. Preserve the original "
                        "results. No model calls yet."
                    ),
                }
            ],
            "interpretation": (
                "preparation only; no successor freeze or execution approval recorded"
            ),
        },
        source_binding={"source_revision": source_revision, "files": source},
        policy_manifests={
            mode: manifest.to_dict()
            for mode, manifest in live_manifests(
                root, source_revision=source_revision, identity=runtime_identity
            ).items()
        },
        predecessor={
            "execution_plan": _binding(root, ORIGINAL_PLAN_PATH, original),
            "outcomes_reused": 0,
            "assignments_replayed": 0,
            "disposition": "completed_replication_preserved_without_reclassification",
        },
        relationship={
            "kind": "post_hoc_same_task_parser_sensitivity",
            "original_replication_plan": _binding(root, ORIGINAL_PLAN_PATH, original),
            "original_replication_results": _binding(
                root,
                "artifacts/grounding-v5-d59-replication-results/report.json",
                json.loads(
                    (
                        root / "artifacts/grounding-v5-d59-replication-results/report.json"
                    ).read_text()
                ),
            ),
            "task_selection": "same inspected seeds, arms, repeats and action horizons",
            "independent_confirmation": False,
            "original_outcomes_reclassified": 0,
            "outcomes_reused": 0,
            "assignments_replayed": 0,
        },
        action_extraction={
            "version": ADAPTER_VERSION,
            "accepted": "bare action JSON or exactly one unlabeled/json fence with optional prose",
            "ambiguity": "reject extra fences and braces, brackets or backticks outside the fence",
            "validation": (
                "unchanged identity first; unique fields, exact schema, integer types and bounds"
            ),
            "repair_calls": 0,
            "format_retries": 0,
            "evidence": (
                "retain canonical response unchanged and journal a hash/span/decision receipt"
            ),
            "original_results": "preserved; diagnostic never changes outcomes or denominators",
        },
        reporting_rule={
            "result_kind": "post_hoc_same_task_parser_sensitivity",
            "original_replication": "reported unchanged alongside the successor",
            "independent_confirmatory_claim_permitted": False,
            "statistics": (
                "retain original allocation and analysis mechanics; label sensitivity only"
            ),
            "task_success_inferred_from_offline_extraction": False,
            "D5_10_verdict_remains_human_owned": True,
        },
    )
    # Give each new assignment its own namespace; never resume the completed run.
    for jobs in (plan["primary_jobs"], plan["reliability_jobs"], plan["pilot"]["jobs"]):
        for job in jobs:
            job["trial_id"] = job["trial_id"].replace("d59-rep-", "d59-extract-", 1)
    for field in (
        "phase_caps",
        "primary_comparison",
        "allocation",
        "runtime",
        "budget",
        "stop_rule",
        "route",
        "authentication_recovery",
        "security_boundary",
        "concurrency",
        "retention",
    ):
        if plan[field] != original[field]:
            raise ValueError(f"successor unexpectedly changed preserved field: {field}")
    return {**plan, "execution_plan_digest": content_digest(plan)}


def changed_fields(original: Any, successor: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Exact JSON pointers for owner review; arrays are whole changed fields."""
    if isinstance(original, dict) and isinstance(successor, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(original.keys() | successor.keys()):
            segment = key.replace("~", "~0").replace("/", "~1")
            pointer = f"{prefix}/{segment}"
            if key not in original or key not in successor:
                changes.append(
                    {
                        "field": pointer,
                        "original_present": key in original,
                        "successor_present": key in successor,
                        "original": content_digest(original[key]) if key in original else None,
                        "successor": content_digest(successor[key]) if key in successor else None,
                    }
                )
            else:
                changes.extend(changed_fields(original[key], successor[key], pointer))
        return changes
    if content_digest(original) == content_digest(successor):
        return []
    return [
        {
            "field": prefix,
            "original": content_digest(original),
            "successor": content_digest(successor),
        }
    ]
