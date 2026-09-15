"""Matched screenshot policies with a source-bound transient retry contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.reliable_transport import RETRY_RULE, ReliableTransport
from pixelgym.grounding.v5.runner import (
    BoundedCallResult,
    DaemonDeadlineExecutor,
    DeadlineExecutor,
    TransportOutcome,
    V5Runner,
)
from pixelgym.grounding.v5.screenshot_memory import (
    ScreenshotMemoryPolicy,
    ScreenshotMemoryRunner,
    build_screenshot_policy_manifest,
)

ROOT = Path(__file__).resolve().parents[3]
REPAIR_SOURCES = (
    "pixelgym/grounding/v5/curl_wire.py",
    "pixelgym/grounding/v5/reliable_transport.py",
    "pixelgym/grounding/v5/reliable_memory.py",
)


def reliable_config(config: ScreenshotPriceConfig) -> ScreenshotPriceConfig:
    return replace(
        config,
        max_model_attempts_per_action=3,
        max_rate_limit_retries_per_action=2,
        max_bounded_retries_per_action=2,
        rate_limit_backoff_base_seconds=5.0,
        rate_limit_backoff_max_seconds=60.0,
    )


def build_reliable_manifest(
    root: Path, *, config: ScreenshotPriceConfig, code_revision: str, retain_screenshots: bool
) -> PolicyManifest:
    base = build_screenshot_policy_manifest(
        root, config=config, code_revision=code_revision, retain_screenshots=retain_screenshots
    )
    sources = {p: "sha256:" + sha256_bytes((root / p).read_bytes()) for p in REPAIR_SOURCES}
    fields = {k: v for k, v in vars(base).items() if k != "policy_id"}
    fields.update(
        transport_retry_rule=RETRY_RULE,
        harness_digest=content_digest({"base": base.harness_digest, "repair": sources}),
        sandbox=replace(
            base.sandbox,
            runtime_digest=content_digest({"base": base.sandbox.runtime_digest, "repair": sources}),
        ),
    )
    return PolicyManifest.build(**fields)


class ReliableMemoryPolicy(ScreenshotMemoryPolicy):
    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        # The transport classifies identified transient errors before this point.
        # Generic empty completions, refusals, and malformed actions are not retried.
        del canonical_response
        return None


class ReliableMemoryRunner(ScreenshotMemoryRunner):
    def __init__(self, *, boundary: Any = lambda _: None, **kwargs: Any) -> None:
        V5Runner.__init__(self, **kwargs)
        self.boundary = boundary
        if not isinstance(self.policy, ReliableMemoryPolicy) or not isinstance(
            self.policy.config, ScreenshotPriceConfig
        ):
            raise TypeError("reliable runner requires its versioned screenshot policy")
        expected = build_reliable_manifest(
            ROOT,
            config=self.policy.config,
            code_revision=self.manifest.code_revision,
            retain_screenshots=self.policy.retain_screenshots,
        )
        if self.manifest != expected:
            raise ValueError("reliable policy manifest differs from executable sources")
        if isinstance(self.transport, ReliableTransport):
            self.deadline_executor = CooldownExecutor(self.transport, self.deadline_executor)

    def _boundary(self, name: str) -> None:
        self.boundary(name)


class CooldownExecutor:
    """Server backoff consumes phase time, not the following request's timeout."""

    def __init__(self, transport: ReliableTransport, inner: DeadlineExecutor | None = None) -> None:
        self.transport, self.inner = transport, inner or DaemonDeadlineExecutor()

    def execute(self, call: Callable[[], object], *, timeout_seconds: float) -> BoundedCallResult:
        if not self.transport.await_ready():
            return BoundedCallResult(
                TransportOutcome("pre_send_failure", failure_code="phase_time_or_retirement_stop"),
                False,
            )
        return self.inner.execute(call, timeout_seconds=timeout_seconds)
