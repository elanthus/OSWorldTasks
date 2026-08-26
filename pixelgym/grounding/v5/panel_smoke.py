"""Plan and execute the exact four-call D5.6 panel integration smoke."""

from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, Partition, content_digest, sha256_bytes
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    PANEL_BY_SLOT,
    PANEL_MAXIMUM_SPEND_USD,
    PRIOR_AGGREGATE_SPEND_USD,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-panel-smoke-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-panel-smoke-result-v1"
PRICE_OBSERVED_AT_UTC = "2026-08-26T22:36:24Z"
SMOKE_ALLOCATIONS = (
    ("A-gemini-stateful", 5001),
    ("B-qwen-stateful", 5005),
    ("C-llama-stateful", 5013),
    ("D-qwen-stateless", 5021),
)


def _git(repository_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def build_plan(repository_root: Path) -> dict[str, Any]:
    revision = _git(repository_root, "rev-parse", "HEAD")
    policies: list[dict[str, Any]] = []
    theoretical_smoke_maximum = Decimal(0)
    for slot, seed in SMOKE_ALLOCATIONS:
        config = PANEL_BY_SLOT[slot]
        task = generate_task(seed)
        if task.seed_record.partition is not Partition.DEVELOPMENT:
            raise ValueError("panel smoke may use development tasks only")
        manifest = build_panel_policy_manifest(
            repository_root, config=config, code_revision=revision
        )
        theoretical_smoke_maximum += config.request_maximum_usd
        policies.append(
            {
                "slot": slot,
                "policy_manifest": manifest.to_dict(),
                "policy_manifest_digest": content_digest(manifest.to_dict()),
                "provider": {
                    "name": "openrouter",
                    "upstream_provider": config.response_provider,
                    "only": [config.provider_route],
                    "quantizations": list(config.quantizations),
                    "allow_fallbacks": False,
                    "automatic_retries": False,
                    "data_collection": "deny",
                    "require_parameters": True,
                },
                "price_record": {
                    "source_url": config.price_source,
                    "observed_at_utc": PRICE_OBSERVED_AT_UTC,
                    "currency": "USD",
                    "prompt_per_token": str(config.prompt_price_per_token_usd),
                    "completion_per_token": str(config.completion_price_per_token_usd),
                    "image_input_billing": "provider input tokens at prompt_per_token",
                    "max_prompt_tokens": 126_976,
                    "max_output_tokens": 4_096,
                    "per_request_theoretical_maximum_usd": str(
                        config.request_maximum_usd
                    ),
                    "unknown_usage_or_price_rule": "fail_closed",
                },
                "task": {
                    "partition": task.seed_record.partition.value,
                    "seed": task.seed,
                    "task_id": task.task_id,
                    "family": task.seed_record.family.value,
                    "actions": 1,
                },
            }
        )
    aggregate_upper_bound = PRIOR_AGGREGATE_SPEND_USD + theoretical_smoke_maximum
    if aggregate_upper_bound > PANEL_MAXIMUM_SPEND_USD:
        raise ValueError("panel smoke theoretical maximum exceeds the approved aggregate cap")
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "one development-only transport/parser/adapter action per frozen D5.6 policy slot; "
            "not calibration evidence"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "dependency_inputs": {
            "pyproject.toml": _file_digest(repository_root / "pyproject.toml"),
            "requirements/platform-py312.lock": _file_digest(
                repository_root / "requirements/platform-py312.lock"
            ),
            "panel_policy.py": _file_digest(
                repository_root / "pixelgym/grounding/v5/panel_policy.py"
            ),
            "runner.py": _file_digest(repository_root / "pixelgym/grounding/v5/runner.py"),
        },
        "policies": policies,
        "caps": {
            **CallCaps(4, 4, 0, 4).to_dict(),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "prior_aggregate_spend_usd": str(PRIOR_AGGREGATE_SPEND_USD),
            "smoke_theoretical_maximum_usd": str(theoretical_smoke_maximum),
            "aggregate_theoretical_upper_bound_usd": str(aggregate_upper_bound),
        },
        "stop_rules": [
            "run policies sequentially in slot order",
            "stop after four total model-attempt reservations",
            "stop after the first transport, identity, cost, parse, adapter, action, or evidence failure",
            "do not retry or replace a failed or incomplete policy smoke",
            "do not expose calibration or confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_smoke(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved panel-smoke plan digest does not match the supplied plan")
    if plan != build_plan(repository_root):
        raise ValueError("panel-smoke plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace panel-smoke output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    ledger = SpendLedger(PANEL_MAXIMUM_SPEND_USD, PRIOR_AGGREGATE_SPEND_USD)
    episode_results: list[dict[str, Any]] = []
    transport_records: list[dict[str, Any]] = []
    try:
        for policy_record in plan["policies"]:
            config = PANEL_BY_SLOT[policy_record["slot"]]
            manifest = build_panel_policy_manifest(
                repository_root,
                config=config,
                code_revision=plan["code_revision"],
            )
            if manifest.to_dict() != policy_record["policy_manifest"]:
                raise ValueError("runtime policy manifest differs from the approved smoke plan")
            task = generate_task(policy_record["task"]["seed"])
            if task.task_id != policy_record["task"]["task_id"]:
                raise ValueError("generated smoke task differs from the approved plan")
            transport = OpenRouterPanelTransport(config, ledger=ledger)
            result = V5Runner(
                journal=journal,
                manifest=manifest,
                transport=transport,
                policy=OpenRouterPanelPolicy(config),
                approved_caps=CallCaps(4, 4, 0, 4),
            ).run(
                trial_id=f"panel-smoke-{config.slot}-{task.task_id}",
                task=task,
                action_limit=1,
            )
            episode_results.append({"slot": config.slot, **result.to_dict()})
            transport_records.extend(transport.records)
            if result.classification != "pilot_action_limit":
                break
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": journal.call_counts()[0],
            "provider_control_requests": journal.call_counts()[1],
            "prior_aggregate_spend_usd": str(PRIOR_AGGREGATE_SPEND_USD),
            "actual_aggregate_spend_usd": str(ledger.spent_usd),
            "smoke_incremental_spend_usd": str(
                ledger.spent_usd - PRIOR_AGGREGATE_SPEND_USD
            ),
            "maximum_aggregate_spend_usd": str(PANEL_MAXIMUM_SPEND_USD),
            "episode_results": episode_results,
            "transport_records": transport_records,
            "journal_integrity": journal.integrity_report(),
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
    finally:
        journal.close()
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
