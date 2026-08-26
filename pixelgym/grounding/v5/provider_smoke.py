"""One-call, development-only OpenRouter smoke planning and execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pixelgym.actions import KEY_ALLOWLIST, InvalidActionError, validate_action
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.providers import GroundingProvider, OpenRouterProvider, ProviderResponse
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import Partition, content_digest, sha256_bytes
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.serialization import canonical_json_bytes

MODEL = "qwen/qwen3.5-flash-02-23"
UPSTREAM_PROVIDER = "alibaba"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
PRICE_SOURCE = "https://openrouter.ai/api/v1/models/qwen/qwen3.5-flash-02-23/endpoints"
PROMPT_PRICE_PER_TOKEN_USD = Decimal("0.000000065")
COMPLETION_PRICE_PER_TOKEN_USD = Decimal("0.00000026")
MAX_PROMPT_TOKENS = 983_616
MAX_OUTPUT_TOKENS = 128
APPROVED_MAXIMUM_SPEND_USD = Decimal("2.00")
DEVELOPMENT_SEED = 5000
SCREENSHOT_PATH = Path("artifacts/grounding-v5-provider-smoke/development-seed-5000-initial.png")
PROMPT_VERSION = "pixelgym-agent-v5-openrouter-action-prompt-v1"
PARSER_VERSION = "pixelgym-agent-v5-json-action-parser-v1"
PLAN_SCHEMA_VERSION = "pixelgym-agent-v5-provider-smoke-plan-v1"
RESULT_SCHEMA_VERSION = "pixelgym-agent-v5-provider-smoke-result-v2"
PRICE_OBSERVED_AT_UTC = "2026-08-26T01:06:43Z"

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {"type": "integer", "enum": [0, 1, 2]},
        "x": {"type": "integer", "minimum": 0, "maximum": 1023},
        "y": {"type": "integer", "minimum": 0, "maximum": 767},
        "key": {"type": "integer", "minimum": 0, "maximum": len(KEY_ALLOWLIST) - 1},
    },
    "required": ["action_type", "x", "y", "key"],
    "additionalProperties": False,
}


def smoke_prompt() -> str:
    task = generate_task(DEVELOPMENT_SEED)
    return (
        "Control the PixelGym v5 application using only the attached 1024x768 screenshot. "
        f"Overall task: {task.instruction} "
        "Choose exactly one next action from the visible current stage. Return only one JSON "
        "object with action_type, x, y, and key. action_type is 0 for NOOP, 1 for CLICK, and "
        "2 for KEY. CLICK coordinates must be integer screenshot pixels with origin at the "
        "upper-left; set key to 0. For KEY, key is the allowlist index; set x and y to 0. "
        "For NOOP, set x, y, and key to 0. Do not use tools or explain the action."
    )


def _file_digest(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def _git(repository_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def build_plan(
    repository_root: Path,
    *,
    maximum_spend_usd: Decimal,
    price_observed_at_utc: str | None = None,
) -> dict[str, Any]:
    if maximum_spend_usd != APPROVED_MAXIMUM_SPEND_USD:
        raise ValueError("maximum spend must match the approved $2.00 cap")
    task = generate_task(DEVELOPMENT_SEED)
    if task.seed_record.partition is not Partition.DEVELOPMENT:
        raise ValueError("smoke seed is not in the development partition")
    screenshot = repository_root / SCREENSHOT_PATH
    theoretical_max = (
        PROMPT_PRICE_PER_TOKEN_USD * MAX_PROMPT_TOKENS
        + COMPLETION_PRICE_PER_TOKEN_USD * MAX_OUTPUT_TOKENS
    )
    if theoretical_max > maximum_spend_usd:
        raise ValueError("the request-level theoretical maximum exceeds the approved spend cap")
    prompt = smoke_prompt()
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": "development-only transport/parser/action smoke; not calibration evidence",
        "provider_calls_made": 0,
        "code_revision": _git(repository_root, "rev-parse", "HEAD"),
        "requires_clean_worktree": True,
        "dependency_inputs": {
            "pyproject.toml": _file_digest(repository_root / "pyproject.toml"),
            "requirements/platform-py312.lock": _file_digest(
                repository_root / "requirements/platform-py312.lock"
            ),
        },
        "provider": {
            "name": "openrouter",
            "endpoint": ENDPOINT,
            "upstream_provider": UPSTREAM_PROVIDER,
            "only": [UPSTREAM_PROVIDER],
            "require_parameters": True,
            "allow_fallbacks": False,
            "data_collection": "deny",
            "automatic_retries": False,
        },
        "model": MODEL,
        "inference_parameters": {
            "temperature": 0,
            "seed": 20260809,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "structured_outputs": True,
        },
        "prompt": {
            "version": PROMPT_VERSION,
            "sha256": "sha256:" + sha256_bytes(prompt.encode("utf-8")),
        },
        "parser_version": PARSER_VERSION,
        "response_schema_digest": content_digest(ACTION_SCHEMA),
        "task": {
            "partition": "development",
            "seed": DEVELOPMENT_SEED,
            "task_id": task.task_id,
            "screenshot_path": SCREENSHOT_PATH.as_posix(),
            "screenshot_sha256": _file_digest(screenshot),
        },
        "caps": {
            "environment_actions": 1,
            "model_attempts": 1,
            "provider_control_requests": 0,
            "provider_wire_requests": 1,
            "maximum_spend_usd": str(maximum_spend_usd),
            "theoretical_request_maximum_usd": str(theoretical_max),
        },
        "price_record": {
            "source_url": PRICE_SOURCE,
            "observed_at_utc": price_observed_at_utc or PRICE_OBSERVED_AT_UTC,
            "currency": "USD",
            "prompt_per_token": str(PROMPT_PRICE_PER_TOKEN_USD),
            "completion_per_token": str(COMPLETION_PRICE_PER_TOKEN_USD),
            "max_prompt_tokens": MAX_PROMPT_TOKENS,
        },
        "approval_required": {
            "exact_plan_sha256": "sha256 of canonical plan bytes",
            "owner": "human",
        },
    }
    return plan


def plan_digest(plan: dict[str, Any]) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes(plan))


def parse_action(raw_response: str | None) -> dict[str, int]:
    if raw_response is None:
        raise ValueError("provider response text is missing")
    value = json.loads(raw_response)
    if not isinstance(value, dict) or set(value) != {"action_type", "x", "y", "key"}:
        raise ValueError("provider response is not one exact action object")
    if any(type(value[key]) is not int for key in ("action_type", "x", "y", "key")):
        raise TypeError("provider action fields must be plain integers")
    return value


def _cost_from_usage(usage: dict[str, Any] | None) -> Decimal:
    if not isinstance(usage, dict) or "cost" not in usage:
        raise ValueError("OpenRouter usage cost is missing")
    try:
        cost = Decimal(str(usage["cost"]))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("OpenRouter usage cost is invalid") from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError("OpenRouter usage cost is invalid")
    return cost


def _response_record(
    *,
    plan: dict[str, Any],
    digest: str,
    response: ProviderResponse,
    maximum_spend: Decimal,
) -> dict[str, Any]:
    raw_response = response.raw_response
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "purpose": plan["purpose"],
        "approved_plan_sha256": digest,
        "provider_calls_made": 1,
        "provider_wire_requests": 1,
        "provider_control_requests": 0,
        "environment_actions": 0,
        "task_id": plan["task"]["task_id"],
        "model": MODEL,
        "response_identity": {
            "response_id_sha256": (
                "sha256:"
                + hashlib.sha256(
                    str(response.provider_metadata["response_id"]).encode("utf-8")
                ).hexdigest()
                if response.provider_metadata.get("response_id") is not None
                else None
            ),
            "response_model": response.provider_metadata.get("response_model"),
            "upstream_provider": response.provider_metadata.get("upstream_provider"),
        },
        "response_sha256": (
            "sha256:" + sha256_bytes(raw_response.encode("utf-8"))
            if isinstance(raw_response, str)
            else None
        ),
        "authoritative_response": {
            "publication_status": "restricted",
            "raw_text": raw_response,
        },
        "publishable_response": None,
        "parser_version": PARSER_VERSION,
        "action_validation": "not_reached",
        "dispatch": None,
        "usage": response.usage,
        "cost_usd": None,
        "maximum_spend_usd": str(maximum_spend),
        "latency_ms": response.latency_ms,
        "classification": "incomplete",
        "failure_code": None,
        "cleanup": {"environment_closed": True},
    }


def execute_smoke(
    repository_root: Path,
    *,
    plan: dict[str, Any],
    approved_plan_sha256: str,
    provider: GroundingProvider | None = None,
) -> dict[str, Any]:
    digest = plan_digest(plan)
    if digest != approved_plan_sha256:
        raise ValueError("approved plan digest does not match the supplied plan")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported smoke plan")
    if plan.get("provider_calls_made") != 0 or plan.get("model") != MODEL:
        raise ValueError("smoke plan identity is invalid")
    if plan.get("provider") != {
        "name": "openrouter",
        "endpoint": ENDPOINT,
        "upstream_provider": UPSTREAM_PROVIDER,
        "only": [UPSTREAM_PROVIDER],
        "require_parameters": True,
        "allow_fallbacks": False,
        "data_collection": "deny",
        "automatic_retries": False,
    }:
        raise ValueError("smoke provider identity is invalid")
    if (
        plan.get("task", {}).get("partition") != "development"
        or plan.get("task", {}).get("seed") != DEVELOPMENT_SEED
    ):
        raise ValueError("smoke plan must use the frozen development seed")
    if (
        plan.get("caps", {}).get("model_attempts") != 1
        or plan.get("caps", {}).get("provider_wire_requests") != 1
    ):
        raise ValueError("smoke plan must authorize exactly one model and wire request")
    if _git(repository_root, "rev-parse", "HEAD") != plan["code_revision"]:
        raise ValueError("source revision differs from the approved plan")
    canonical_plan = build_plan(
        repository_root,
        maximum_spend_usd=APPROVED_MAXIMUM_SPEND_USD,
    )
    if plan != canonical_plan:
        raise ValueError("smoke plan does not match the canonical request configuration")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked worktree must be clean before the provider request")

    screenshot_path = repository_root / SCREENSHOT_PATH
    if _file_digest(screenshot_path) != plan["task"]["screenshot_sha256"]:
        raise ValueError("approved screenshot digest mismatch")
    maximum_spend = Decimal(plan["caps"]["maximum_spend_usd"])
    if maximum_spend != APPROVED_MAXIMUM_SPEND_USD:
        raise ValueError("smoke plan spend cap differs from the approved $2.00 cap")
    if Decimal(plan["caps"]["theoretical_request_maximum_usd"]) > maximum_spend:
        raise ValueError("approved request can exceed its spend cap")

    task = generate_task(int(plan["task"]["seed"]))
    backend = V5FakeBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    try:
        observation, info = env.reset(seed=task.seed)
        with Image.open(screenshot_path) as approved_image:
            if not np.array_equal(observation, np.asarray(approved_image.convert("RGB"))):
                raise ValueError("runtime initial screenshot differs from the approved image")
        if info["task_id"] != plan["task"]["task_id"]:
            raise ValueError("runtime task differs from the approved task")

        if provider is None:
            provider = OpenRouterProvider(
                environment={
                    "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
                    "OPENROUTER_MODEL": MODEL,
                },
                request_parameters={"max_tokens": MAX_OUTPUT_TOKENS},
                provider_routing={
                    "only": [UPSTREAM_PROVIDER],
                    "allow_fallbacks": False,
                    "data_collection": "deny",
                },
            )
        response = provider.invoke(
            image_path=screenshot_path,
            prompt=smoke_prompt(),
            schema=ACTION_SCHEMA,
        )
        result = _response_record(
            plan=plan,
            digest=digest,
            response=response,
            maximum_spend=maximum_spend,
        )
        try:
            cost = _cost_from_usage(response.usage)
        except ValueError:
            result.update(
                classification="evidence_integrity_failure",
                failure_code="missing_or_invalid_usage_cost",
            )
            return result
        result["cost_usd"] = str(cost)
        if cost > maximum_spend:
            result.update(
                classification="spend_cap_violation",
                failure_code="provider_cost_exceeded_approved_cap",
            )
            return result
        if response.request_failure is not None:
            result.update(
                classification="transport_failure",
                failure_code=response.request_failure,
            )
            return result
        if (
            response.provider_metadata.get("response_model") != MODEL
            or str(response.provider_metadata.get("upstream_provider", "")).lower()
            != UPSTREAM_PROVIDER
        ):
            result.update(
                classification="evidence_integrity_failure",
                failure_code="provider_or_model_identity_mismatch",
            )
            return result
        try:
            action = parse_action(response.raw_response)
            validated = validate_action(env.action_space, action)
        except (InvalidActionError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result.update(
                classification="invalid_output",
                failure_code=type(exc).__name__,
                action_validation="rejected",
            )
            return result
        canonical_action = {
            "action_type": validated.action_type,
            "x": validated.x,
            "y": validated.y,
            "key": validated.key,
        }
        _next_observation, reward, terminated, truncated, result_info = env.step(canonical_action)
        result.update(
            environment_actions=1,
            task_id=result_info["task_id"],
            publishable_response=canonical_action,
            action_validation="accepted",
            dispatch={
                "reward": reward,
                "terminated": terminated,
                "truncated": truncated,
                "backend_diagnostic": backend.read_privileged_diagnostic()["event"],
            },
            classification="dispatched",
        )
        return result
    finally:
        env.close()


def write_fresh_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def publishable_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the console/publishing derivative without restricted provider output."""
    derivative = dict(result)
    derivative.pop("authoritative_response", None)
    return derivative
