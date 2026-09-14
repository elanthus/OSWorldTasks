"""OpenRouter successor for the CLI Haiku screenshot policy, with explicit billing."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import (
    CliMemoryPolicy,
    build_memory_manifest,
    memory_prompt,
)
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.panel_policy import (
    MAX_OUTPUT_TOKENS,
    OpenRouterPanelPolicy,
    build_panel_policy_manifest,
)
from pixelgym.grounding.v5.request_budget import request_bound

MODEL = "anthropic/claude-haiku-4.5"
PRICE_SOURCE = "https://openrouter.ai/api/v1/models/anthropic/claude-haiku-4.5/endpoints"


def config_from_snapshot(snapshot: dict[str, Any]) -> ScreenshotPriceConfig:
    data = snapshot["data"]
    if data["id"] != MODEL:
        raise ValueError("price snapshot model mismatch")
    endpoints = [e for e in data["endpoints"] if e["tag"] == "anthropic"]
    if len(endpoints) != 1:
        raise ValueError("one direct Anthropic endpoint required")
    e = endpoints[0]
    if e["provider_name"] != "Anthropic" or e["context_length"] != 200000:
        raise ValueError("unexpected provider or context")
    if not {"reasoning", "response_format", "structured_outputs", "max_tokens"} <= set(
        e["supported_parameters"]
    ):
        raise ValueError("endpoint lacks required request controls")
    pricing = e["pricing"]
    if Decimal(pricing["prompt"]) != Decimal(".000001") or Decimal(
        pricing["completion"]
    ) != Decimal(".000005"):
        raise ValueError("route prices changed; reapprove budget plan")
    return ScreenshotPriceConfig(
        slot="pr196-haiku-openrouter",
        model=MODEL,
        provider_route="anthropic",
        response_provider="Anthropic",
        prompt_price_per_token_usd=Decimal(pricing["prompt"]),
        completion_price_per_token_usd=Decimal(pricing["completion"]),
        price_source=PRICE_SOURCE,
        adapter=IDENTITY_ADAPTER,
        coordinate_input_convention="integer-pixel/1024x768",
        stateful=False,
        temperature=None,
        max_model_attempts_per_action=2,
        max_rate_limit_retries_per_action=1,
        max_bounded_retries_per_action=1,
        request_deadline_seconds=190,
        enforce_provider_price_cap=True,
        upstream_context_length=200000,
    )


class HaikuOpenRouterPolicy(CliMemoryPolicy):
    def __init__(self, config: ScreenshotPriceConfig, *, retain_screenshots: bool):
        super().__init__(claude.ClaudeCodePolicy(), retain_screenshots=retain_screenshots)
        self.config = config
        self.http_parser = OpenRouterPanelPolicy(config)

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        source = super().build_request(state, screenshot)
        images = [*source["image_history"], source]
        content = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + i["image_png_base64"]},
            }
            for i in images
        ]
        content.append({"type": "text", "text": source["prompt"]})
        return {
            "model": self.config.model,
            "provider": self.config.provider_parameters(),
            "messages": [
                {"role": "system", "content": claude.SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "gui_action",
                    "strict": True,
                    "schema": claude.ACTION_SCHEMA,
                },
            },
            "max_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"enabled": True},
        }

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        response = json.loads(canonical_response)
        # Decode only the declared whole JSON envelope, preserving stored raw response.
        response["content"] = json.dumps(claude._decode_action_content(response["content"]))
        return self.http_parser.parse(json.dumps(response).encode(), state)


def full_context_bound(request: dict[str, Any], config: ScreenshotPriceConfig) -> dict[str, Any]:
    proof = request_bound(request, config)  # Validate the bounded PNG/message surface.
    return {
        **proof,
        "version": "haiku-full-context-plus-output-bound-v1",
        "input_units_bound": config.upstream_context_length,
        "request_maximum_usd": str(config.request_maximum_usd),
    }


def build_manifest(
    root: Path, config: ScreenshotPriceConfig, revision: str, retain: bool
) -> PolicyManifest:
    base = build_panel_policy_manifest(root, config=config, code_revision=revision)
    base = build_memory_manifest(root, base, retain_screenshots=retain)
    sources = {
        p: "sha256:" + sha256_bytes((root / "pixelgym/grounding/v5" / p).read_bytes())
        for p in (
            "haiku_openrouter.py",
            "claude_code_policy.py",
            "reliable_transport.py",
            "curl_wire.py",
            "request_budget.py",
        )
    }
    fields = {k: v for k, v in vars(base).items() if k != "policy_id"}
    fields.update(
        harness_digest=content_digest({"base": base.harness_digest, "sources": sources}),
        sandbox=replace(
            base.sandbox,
            runtime_digest=content_digest(
                {"base": base.sandbox.runtime_digest, "sources": sources}
            ),
        ),
        parser_version="haiku-openrouter-native-json-envelope-v1",
        system_prompt_digest=content_digest(
            {"system": claude.SYSTEM_PROMPT, "prompt": memory_prompt("<instruction>", [])}
        ),
        transport_retry_rule="openrouter-one-transient-retry-retain-unknown-cost-v1",
        inference_parameters=tuple(
            (k, v)
            for k, v in base.inference_parameters
            if k not in {"runner_retries", "temperature", "model_seed"}
        )
        + (
            ("reasoning", "enabled-default"),
            ("request_bound", "full-context-plus-output"),
            ("seed", "omitted-unsupported-route"),
        ),
    )
    return PolicyManifest.build(**fields)
