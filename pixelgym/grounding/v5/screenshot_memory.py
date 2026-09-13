"""Durable, episode-local screenshot history and its matched stateless control.

Observation reduction is pure and happens before the existing attempt journal
persists the pre-call checkpoint. Recovery therefore needs no in-process cache.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import PolicyManifest, content_digest, sha256_bytes
from pixelgym.grounding.v5.memory_backend import MemoryBackend
from pixelgym.grounding.v5.openrouter_policy import _png_data_url
from pixelgym.grounding.v5.panel_policy import (
    OpenRouterPanelPolicy,
    PanelPolicyConfig,
    build_panel_policy_manifest,
    system_prompt,
)
from pixelgym.grounding.v5.runner import PolicyVisibleResult, V5Runner
from pixelgym.serialization import canonical_json_bytes

OBSERVATION_REDUCER_VERSION = "pixelgym-agent-v5-observed-screenshots-v1"
MAX_OBSERVED_FRAMES = 32


def require_clean_tracked_worktree(repository_root: Path) -> None:
    """Match the execution drivers' source guard before hashing a manifest."""
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repository_root,
        text=True,
    )
    if dirty.strip():
        raise ValueError("tracked worktree must be clean before recording policy evidence")


def memory_system_prompt(config: PanelPolicyConfig) -> str:
    prompt = system_prompt(replace(config, stateful=False, controlled_history_prompt=True))
    return prompt.replace(
        "using only the current screenshot,", "using only the supplied screenshots,"
    ) + (
        " Screenshots are in chronological order, with your intervening actions when available."
        " The last screenshot is current. Earlier screenshots may be absent."
        " Recorded CLICK actions use native 1024x768 screenshot pixels;"
        " generate the next action using the output coordinate convention above."
    )


class ScreenshotMemoryPolicy(OpenRouterPanelPolicy):
    def __init__(self, config: PanelPolicyConfig, *, retain_screenshots: bool) -> None:
        # The inherited parser/transport settings are shared. None of the old
        # visible-action reducer's metadata is supplied to either new arm.
        super().__init__(replace(config, stateful=False, controlled_history_prompt=True))
        self.retain_screenshots = retain_screenshots

    def reset(self, task_instruction: str) -> bytes:
        value: dict[str, Any] = {"instruction": task_instruction}
        if self.retain_screenshots:
            value.update({"frames": [], "actions": []})
        return canonical_json_bytes(value)

    def observe_screenshot(self, state: bytes, screenshot: bytes) -> bytes:
        """Add the current legitimate observation once, without private mutable state."""
        image_url = _png_data_url(screenshot)
        if not self.retain_screenshots:
            return state
        value = json.loads(state)
        frames, actions = value["frames"], value["actions"]
        frame = {"rgb_digest": "sha256:" + sha256_bytes(screenshot), "image_url": image_url}
        if len(frames) == len(actions) + 1:
            if frames[-1] != frame:
                raise ValueError("observed screenshot changed before dispatch")
            return state
        if len(frames) != len(actions) or len(frames) >= MAX_OBSERVED_FRAMES:
            raise ValueError("screenshot history exceeds the frozen action boundary")
        frames.append(frame)
        return canonical_json_bytes(value)

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        current = _png_data_url(screenshot)
        if self.retain_screenshots:
            if len(value["frames"]) != len(value["actions"]) + 1:
                raise ValueError(
                    "current screenshot must be checkpointed before request construction"
                )
            if value["frames"][-1] != {
                "rgb_digest": "sha256:" + sha256_bytes(screenshot),
                "image_url": current,
            }:
                raise ValueError("request screenshot does not match the observation checkpoint")
            frames, actions = value["frames"], value["actions"]
        else:
            frames, actions = [{"image_url": current}], []
        request = super().build_request(
            canonical_json_bytes({"instruction": value["instruction"]}), screenshot
        )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": f"Overall task: {value['instruction']}"}
        ]
        for index, frame in enumerate(frames):
            content.append({"type": "image_url", "image_url": {"url": frame["image_url"]}})
            if index < len(actions):
                content.append(
                    {
                        "type": "text",
                        "text": "Your action: " + json.dumps(actions[index], sort_keys=True),
                    }
                )
        content.append(
            {"type": "text", "text": "Choose exactly one next action from the current screenshot."}
        )
        request["messages"] = [
            {"role": "system", "content": memory_system_prompt(self.config)},
            {"role": "user", "content": content},
        ]
        return request

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: PolicyVisibleResult
    ) -> bytes:
        del result
        if not self.retain_screenshots:
            return state
        value = json.loads(state)
        if len(value["frames"]) != len(value["actions"]) + 1:
            raise ValueError("dispatch lacks its observed screenshot")
        value["actions"].append(action)
        return canonical_json_bytes(value)


class ScreenshotMemoryRunner(V5Runner):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not isinstance(self.policy, ScreenshotMemoryPolicy):
            raise TypeError("screenshot runner requires the frozen screenshot policy")
        expected = build_screenshot_policy_manifest(
            Path(__file__).resolve().parents[3],
            config=self.policy.config,
            code_revision=self.manifest.code_revision,
            retain_screenshots=self.policy.retain_screenshots,
        )
        if self.manifest != expected:
            raise ValueError("screenshot policy does not match its frozen manifest")

    def _preflight(self, task: Any, backend: V5FakeBackend, *, required_action_limit: int) -> None:
        if not isinstance(backend, MemoryBackend):
            raise TypeError("screenshot-memory evaluation requires the deferred-feedback backend")
        # Both arms admit the same horizons. Reject before reset or any model
        # request instead of letting only history fail at its 33rd observation.
        if required_action_limit > MAX_OBSERVED_FRAMES:
            raise ValueError("assigned action limit exceeds the frozen screenshot capacity")
        super()._preflight(task, backend, required_action_limit=required_action_limit)

    def _act(
        self,
        *,
        trial_id: str,
        step_index: int,
        env: PixelGuiEnv,
        backend: V5FakeBackend,
        state: bytes,
        observation: Any,
    ) -> dict[str, Any]:
        if not isinstance(self.policy, ScreenshotMemoryPolicy):
            raise TypeError("screenshot runner requires the frozen screenshot policy")
        observed_state = self.policy.observe_screenshot(state, observation.tobytes())
        # super()._act durably persists these exact bytes before any wire request.
        # An interruption before that reservation sends nothing and can rederive
        # the same bytes from the prior checkpoint and the bound current pixels.
        return super()._act(
            trial_id=trial_id,
            step_index=step_index,
            env=env,
            backend=backend,
            state=observed_state,
            observation=observation,
        )


def build_screenshot_policy_manifest(
    repository_root: Path,
    *,
    config: PanelPolicyConfig,
    code_revision: str,
    retain_screenshots: bool,
) -> PolicyManifest:
    require_clean_tracked_worktree(repository_root)
    config = replace(config, stateful=False, controlled_history_prompt=True)
    base = build_panel_policy_manifest(repository_root, config=config, code_revision=code_revision)
    fields = {key: value for key, value in vars(base).items() if key != "policy_id"}
    source_digest = "sha256:" + sha256_bytes(
        (repository_root / "pixelgym/grounding/v5/screenshot_memory.py").read_bytes()
    )
    runtime_digest = content_digest(
        {"base_runtime": base.sandbox.runtime_digest, "screenshot_policy": source_digest}
    )
    mode = "history" if retain_screenshots else "stateless"
    fields.update(
        harness_digest=content_digest(
            {"base_runner": base.harness_digest, "observation_runner": source_digest}
        ),
        sandbox=replace(base.sandbox, runtime_digest=runtime_digest),
        system_prompt_digest=content_digest(memory_system_prompt(config)),
        state_reducer_version=f"{OBSERVATION_REDUCER_VERSION}-{mode}",
        memory_policy_version=f"pixelgym-agent-v5-screenshot-{mode}-v1",
        inference_parameters=(
            *base.inference_parameters,
            ("max_observed_frames", str(MAX_OBSERVED_FRAMES)),
        ),
        context_limit=getattr(config, "upstream_context_length", base.context_limit),
    )
    return PolicyManifest.build(**fields)
