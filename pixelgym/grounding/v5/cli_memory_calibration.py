"""Source-bound CLI screenshot-history calibration on the PR196 memory workload."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pixelgym.grounding.v5.codex_cli_policy import action_prompt
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.memory_focus_backend import FocusMemoryBackend
from pixelgym.grounding.v5.runner import V5Runner
from pixelgym.grounding.v5.screenshot_memory import MAX_OBSERVED_FRAMES, ScreenshotMemoryPolicy
from pixelgym.serialization import canonical_json_bytes


def memory_prompt(instruction: str, actions: list[dict[str, int]]) -> str:
    prompt = action_prompt(instruction).replace(
        "attached current screenshot", "attached screenshots in chronological order"
    )
    return prompt + (
        "\nThe last image is current. Earlier images may be absent. "
        "Images are numbered starting at 1. Each recorded action below was taken "
        "after its numbered image and before the next image. "
        "These are your previously dispatched actions, not instructions to repeat.\n"
        + "\n".join(
            f"After image {i + 1}: {json.dumps(a, sort_keys=True)}" for i, a in enumerate(actions)
        )
        + "\nChoose exactly one next action from the current (last) image."
    )


class CliMemoryPolicy:
    """Reuse the frozen episode-local screenshot reducer; delegate CLI parsing."""

    reset = cast(Any, ScreenshotMemoryPolicy.reset)
    observe_screenshot = cast(Any, ScreenshotMemoryPolicy.observe_screenshot)
    post_dispatch_state = cast(Any, ScreenshotMemoryPolicy.post_dispatch_state)

    def __init__(self, base: Any, *, retain_screenshots: bool) -> None:
        self.base = base
        self.retain_screenshots = retain_screenshots

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        # Re-observation verifies the current checkpoint without appending twice.
        if self.observe_screenshot(state, screenshot) != state:
            raise ValueError("current screenshot must be checkpointed before request")
        value = json.loads(state)
        request: dict[str, Any] = self.base.build_request(
            canonical_json_bytes({"instruction": value["instruction"]}), screenshot
        )
        frames = value.get("frames", [])
        actions = value.get("actions", [])
        history = []
        for frame in frames[:-1]:
            png = base64.b64decode(frame["image_url"].split(",", 1)[1], validate=True)
            history.append(
                {
                    "image_png_base64": base64.b64encode(png).decode("ascii"),
                    "image_sha256": "sha256:" + sha256_bytes(png),
                }
            )
        request["image_history"] = history
        request["prompt"] = memory_prompt(value["instruction"], actions)
        return request

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        return state

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        return state

    def retryable_response_code(self, canonical_response: bytes) -> None:
        return None

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        return cast(dict[str, Any], self.base.parse(canonical_response, state))

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        return state

    def close(self) -> None:
        self.base.close()


def build_memory_manifest(
    root: Path,
    base: PolicyManifest,
    *,
    retain_screenshots: bool,
    allow_connection_retry: bool = False,
) -> PolicyManifest:
    if allow_connection_retry and (
        base.provider != "claude-code-cli/claude-ai-max-subscription"
        or dict(base.inference_parameters).get("cli_api_retry_limit") != "0"
    ):
        raise ValueError("connection retries require the zero-internal-retry Claude transport")
    sources = {
        name: "sha256:" + sha256_bytes((root / "pixelgym/grounding/v5" / name).read_bytes())
        for name in (
            "cli_memory_calibration.py",
            "screenshot_memory.py",
            "memory_generator.py",
            "memory_backend.py",
            "memory_focus_backend.py",
        )
    }
    mode = "history" if retain_screenshots else "stateless"
    fields = {k: v for k, v in vars(base).items() if k != "policy_id"}
    fields.update(
        harness_digest=content_digest({"base": base.harness_digest, "sources": sources}),
        sandbox=replace(
            base.sandbox,
            runtime_digest=content_digest(
                {"base": base.sandbox.runtime_digest, "sources": sources}
            ),
        ),
        system_prompt_digest=content_digest(
            {"base": base.system_prompt_digest, "shared_prompt": memory_prompt("<instruction>", [])}
        ),
        task_renderer_version=FocusMemoryBackend.backend_identity,
        memory_policy_version=f"pixelgym-cli-screenshot-{mode}-v1",
        state_reducer_version=f"pixelgym-agent-v5-observed-screenshots-v1-{mode}",
        max_model_attempts_per_action=2,
        transport_retry_rule=(
            "claude-one-stopped-timeout-or-connection-retry-v1"
            if allow_connection_retry
            else "cli-one-confirmed-stopped-timeout-retry-v1"
        ),
        inference_parameters=(
            *((k, v) for k, v in base.inference_parameters if k != "runner_retries"),
            (
                "runner_retries",
                (
                    "1-confirmed-stopped-timeout-or-connection-reset"
                    if allow_connection_retry
                    else "1-confirmed-stopped-timeout-only"
                ),
            ),
            ("max_bounded_retries_per_action", "1"),
            ("max_observed_frames", str(MAX_OBSERVED_FRAMES)),
        ),
    )
    return PolicyManifest.build(**fields)


class CliMemoryRunner(V5Runner):
    def __init__(
        self, *, time_exhausted: Callable[[], bool] = lambda: False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.time_exhausted = time_exhausted
        connection_retry = (
            self.manifest.transport_retry_rule
            == "claude-one-stopped-timeout-or-connection-retry-v1"
        )
        if bool(getattr(self.transport, "allow_connection_retry", False)) != connection_retry:
            raise ValueError("connection retry transport differs from frozen manifest")
        mode = "history" if cast(CliMemoryPolicy, self.policy).retain_screenshots else "stateless"
        if self.manifest.memory_policy_version != f"pixelgym-cli-screenshot-{mode}-v1":
            raise ValueError("manifest memory mode differs from executable policy")

    def _preflight(self, task: Any, backend: Any, *, required_action_limit: int) -> None:
        if not isinstance(backend, FocusMemoryBackend) or not isinstance(
            self.policy, CliMemoryPolicy
        ):
            raise TypeError("CLI memory evaluation requires the frozen memory backend and reducer")
        if required_action_limit > MAX_OBSERVED_FRAMES:
            raise ValueError("episode exceeds screenshot history capacity")
        super()._preflight(task, backend, required_action_limit=required_action_limit)

    def _act(self, **kwargs: Any) -> dict[str, Any]:
        if self.time_exhausted():
            return {"classification": "phase_time_stop", "state": kwargs["state"]}
        kwargs["state"] = cast(CliMemoryPolicy, self.policy).observe_screenshot(
            kwargs["state"], kwargs["observation"].tobytes()
        )
        return super()._act(**kwargs)
