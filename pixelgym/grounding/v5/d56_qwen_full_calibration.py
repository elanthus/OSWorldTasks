"""Plan and execute a Qwen3-VL successor after the frozen 429 outcome."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import CallCaps, content_digest
from pixelgym.grounding.v5.d56_c_calibration import _validated_frozen_bcd_evidence
from pixelgym.grounding.v5.d56_calibration import (
    CALIBRATION_MANIFEST,
    CONSECUTIVE_FAILURE_LIMIT,
    EXPECTED_TASK_COUNT,
    ConsecutiveFailureBreaker,
    _calibration_manifest,
    _file_digest,
    _git,
    _validated_smoke_evidence,
)
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.panel_policy import (
    BOUNDED_RETRY_STOP_RULE,
    QWEN_STATEFUL_RETRY_SUCCESSOR,
    OpenRouterPanelPolicy,
    OpenRouterPanelTransport,
    SpendLedger,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.planning import call_cap_plan, load_partition_manifests
from pixelgym.grounding.v5.runner import V5Runner

PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-d56-qwen-full-calibration-result-v1"

ENDPOINT_METADATA_OBSERVED_AT_UTC = "2026-08-28T13:34:08Z"


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def build_plan(
    repository_root: Path,
    *,
    smoke_output_directory: Path,
    frozen_bcd_output_directory: Path,
    maximum_spend_usd: Decimal,
) -> dict[str, Any]:
    if maximum_spend_usd <= 0:
        raise ValueError("maximum run spend must be positive")
    revision = _git(repository_root, "rev-parse", "HEAD")
    partition = _calibration_manifest(repository_root)
    smoke_evidence = _validated_smoke_evidence(repository_root, smoke_output_directory)
    # The frozen B/C/D evidence pins the withdrawn 429 attempt so this successor
    # cannot replay or reinterpret it. It carries no spend into this run.
    qwen_predecessor = _validated_frozen_bcd_evidence(
        repository_root, frozen_bcd_output_directory
    )
    action_cap = sum(record["max_episode_steps"] for record in partition["records"])
    config = QWEN_STATEFUL_RETRY_SUCCESSOR
    manifest = build_panel_policy_manifest(repository_root, config=config, code_revision=revision)
    partition_manifests = load_partition_manifests(
        repository_root / "artifacts/grounding-v5-manifests",
        calibration_manifest=repository_root / CALIBRATION_MANIFEST,
    )
    phase_call_cap_plan = call_cap_plan(
        manifest,
        partition_manifests=partition_manifests,
        approved_calibration_manifest_digest=partition["manifest_digest"],
    )
    attempt_cap = action_cap * manifest.max_model_attempts_per_action
    theoretical_maximum = config.request_maximum_usd * attempt_cap
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": (
            "evaluate the Qwen3-VL stateful retry successor on all fifty frozen D5.6 "
            "calibration tasks as a distinct run"
        ),
        "provider_calls_made": 0,
        "code_revision": revision,
        "requires_clean_tracked_worktree": True,
        "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
        "calibration_partition": {
            "path": CALIBRATION_MANIFEST.as_posix(),
            "file_sha256": _file_digest(repository_root / CALIBRATION_MANIFEST),
            "manifest_digest": partition["manifest_digest"],
            "source_manifest_digest": partition["derivation"]["source_manifest_digest"],
            "pilot_plan_digest": partition["derivation"]["pilot_plan_digest"],
            "episode_count": EXPECTED_TASK_COUNT,
            "action_cap": action_cap,
            "exclusion_rule": partition["derivation"]["exclusion_rule"],
            "excluded_seeds": partition["derivation"]["excluded_seeds"],
            "replacement_seeds": partition["derivation"]["replacement_seeds"],
        },
        "policy": {
            "slot": config.slot,
            "policy_manifest": manifest.to_dict(),
            "policy_manifest_digest": content_digest(manifest.to_dict()),
            "phase_call_cap_plan": phase_call_cap_plan,
            "provider": {
                "name": "openrouter",
                "upstream_provider": config.response_provider,
                "only": [config.provider_route],
                "allow_fallbacks": False,
                "automatic_retries": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
            "price_record": {
                "source_url": config.price_source,
                "observed_at_utc": ENDPOINT_METADATA_OBSERVED_AT_UTC,
                "currency": "USD",
                "prompt_per_token": str(config.prompt_price_per_token_usd),
                "completion_per_token": str(config.completion_price_per_token_usd),
                "image_input_billing": "provider input tokens at prompt_per_token",
                "max_prompt_tokens": 126_976,
                "max_output_tokens": 4_096,
                "endpoint_context_length": 131_072,
                "endpoint_tag": config.provider_route,
                "endpoint_status": 0,
                "supports_response_format": True,
                "supports_structured_outputs": True,
                "unknown_usage_or_price_rule": "fail_closed",
            },
        },
        "run_continuation": {
            "rule": (
                "record every non-normal terminal classification as a failed assignment "
                "and continue to the next task so all fifty stay in the denominator"
            ),
            "consecutive_failure_limit": CONSECUTIVE_FAILURE_LIMIT,
            "hard_stop_conditions": [
                "run spend ledger blocked",
                "policy or request identity mismatch",
                "missing, invalid, or exceeded price guard",
                "non-retryable HTTP status",
                "evidence-integrity failure",
            ],
            "retryable_send_outcomes": [
                "http_429_rate_limit",
                "transient transport fault (dropped connection, timeout, retryable 5xx, unreadable envelope)",
                "zero_completion_error",
            ],
            "unobservable_charge_rule": (
                "reserve the per-request theoretical maximum against this run's ledger "
                "for every send whose charge cannot be observed"
            ),
        },
        "caps": {
            **CallCaps(action_cap, attempt_cap, 0, attempt_cap).to_dict(),
            "maximum_run_spend_usd": str(maximum_spend_usd),
            "spend_lineage": (
                "per-run: this run's ledger starts at zero and is bounded only by the "
                "approved maximum_run_spend_usd; no prior run's spend is carried in"
            ),
            "per_request_theoretical_maximum_usd": str(config.request_maximum_usd),
            "uncapped_run_theoretical_maximum_usd": str(theoretical_maximum),
            "enforcement": (
                "before each wire request, reserve the pinned Alibaba endpoint's worst-case "
                "request cost against this run's own ledger; stop before a request "
                "that cannot fit"
            ),
        },
        "successful_smoke_evidence": smoke_evidence,
        "frozen_qwen_429_predecessor": qwen_predecessor,
        "task_order": [
            {
                "ordinal": ordinal,
                "seed": record["seed_record"]["seed"],
                "task_id": record["task_id"],
                "family": record["seed_record"]["family"],
                "max_episode_steps": record["max_episode_steps"],
            }
            for ordinal, record in enumerate(partition["records"])
        ],
        "stop_rules": [
            "run all fifty tasks in frozen manifest order",
            "continue after success termination or step-limit truncation so assigned tasks remain in the denominator",
            BOUNDED_RETRY_STOP_RULE,
            "retain every invalid or unparseable model output, record it as a failed assignment, and continue to the next task",
            "continue after a settled per-task transport or infrastructure failure so the assignment stays in the denominator",
            f"stop after {CONSECUTIVE_FAILURE_LIMIT} consecutive non-normal terminal classifications",
            "stop immediately on an identity, price-guard, non-retryable HTTP, or evidence-integrity failure",
            "stop before any request whose per-request theoretical maximum cannot fit under the shared ten-dollar ledger",
            "do not resume or replay the frozen Qwen 429 request; this is a distinct successor run",
            "do not resume, retry, replace, or reinterpret any frozen Gemini request or assignment",
            "do not expose confirmatory tasks",
        ],
        "approval_required": {
            "owner": "human",
            "exact_plan_sha256": "sha256 of canonical plan bytes",
        },
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan)


def execute_calibration(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    smoke_output_directory: Path,
    frozen_bcd_output_directory: Path,
    output_directory: Path,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved Qwen full calibration digest does not match the plan")
    maximum_spend_usd = Decimal(plan["caps"]["maximum_run_spend_usd"])
    if plan != build_plan(
        repository_root,
        smoke_output_directory=smoke_output_directory,
        frozen_bcd_output_directory=frozen_bcd_output_directory,
        maximum_spend_usd=maximum_spend_usd,
    ):
        raise ValueError("Qwen full calibration does not match canonical configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before calibration provider requests")
    if output_directory.exists():
        raise FileExistsError(f"refusing to replace Qwen calibration output: {output_directory}")
    output_directory.mkdir(parents=True)
    journal = V5AttemptJournal(output_directory / "attempts.sqlite")
    # The approved plan carries this run's entire budget; nothing is inherited.
    ledger = SpendLedger(maximum_spend_usd, Decimal(0))
    approved_caps = CallCaps(
        plan["caps"]["environment_action_cap"],
        plan["caps"]["model_attempt_cap"],
        plan["caps"]["provider_control_request_cap"],
        plan["caps"]["provider_wire_request_cap"],
    )
    config = QWEN_STATEFUL_RETRY_SUCCESSOR
    manifest = build_panel_policy_manifest(
        repository_root, config=config, code_revision=plan["code_revision"]
    )
    transport: OpenRouterPanelTransport | None = None
    breaker = ConsecutiveFailureBreaker(CONSECUTIVE_FAILURE_LIMIT)
    episode_results: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    try:
        if manifest.to_dict() != plan["policy"]["policy_manifest"]:
            raise ValueError("runtime policy manifest differs from approved calibration plan")
        transport = OpenRouterPanelTransport(config, ledger=ledger)
        for task_record in plan["task_order"]:
            task = generate_task(task_record["seed"])
            if task.task_id != task_record["task_id"]:
                raise ValueError("generated calibration task differs from approved plan")
            result = V5Runner(
                journal=journal,
                manifest=manifest,
                transport=transport,
                policy=OpenRouterPanelPolicy(config),
                approved_caps=approved_caps,
            ).run(
                trial_id=f"d56-qwen-v2-{task_record['ordinal']:02d}-{task.task_id}",
                task=task,
            )
            episode_results.append({"slot": config.slot, **result.to_dict()})
            if breaker.record(result.classification):
                break
            if ledger.blocked:
                breaker.trip("run_spend_ledger_blocked")
                break
    except Exception as exc:
        execution_error = {"type": type(exc).__name__}
        raise
    finally:
        classifications = Counter(result["classification"] for result in episode_results)
        transport_records = [] if transport is None else list(transport.records)
        integrity = journal.integrity_report()
        call_counts = journal.call_counts()
        journal.close()
        summary = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "purpose": plan["purpose"],
            "approved_plan_sha256": digest,
            "code_revision": plan["code_revision"],
            "provider_calls_made": ledger.wire_requests_sent,
            "provider_wire_requests": ledger.wire_requests_sent,
            "model_attempt_reservations": call_counts[0],
            "provider_control_requests": call_counts[1],
            "run_spend_usd": str(ledger.spent_usd),
            "budget_accounted_run_spend_usd": str(ledger.budget_accounted_spend_usd),
            "unknown_charge_reservation_usd": str(ledger.unknown_reservation_usd),
            "unknown_charge_outcomes": ledger.unknown_charge_outcomes,
            "run_spend_ledger_blocked": ledger.blocked,
            "run_continuation": breaker.to_dict(),
            "remaining_run_spend_usd": str(
                maximum_spend_usd - ledger.budget_accounted_spend_usd
            ),
            "maximum_run_spend_usd": str(maximum_spend_usd),
            "assigned_policy_task_pairs": EXPECTED_TASK_COUNT,
            "attempted_policy_task_pairs": len(episode_results),
            "successful_policy_task_pairs": sum(result["success"] for result in episode_results),
            "completed_all_assigned_pairs": len(episode_results) == EXPECTED_TASK_COUNT,
            "classifications": dict(sorted(classifications.items())),
            "episode_results": episode_results,
            "execution_error": execution_error,
            "transport_records": transport_records,
            "successful_smoke_evidence": plan["successful_smoke_evidence"],
            "frozen_qwen_429_predecessor": plan["frozen_qwen_429_predecessor"],
            "journal_integrity": integrity,
            "publication_status": "restricted_raw_responses_in_local_journal",
            "cleanup": {"journal_closed": True, "policy_and_environments_closed": True},
        }
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary
