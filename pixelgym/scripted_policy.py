"""Stdlib-only scripted policy shared by the v5 runner and isolated serving worker.

Keep host journals, environment code, and native database libraries out of this import path.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from pixelgym.serialization import canonical_json_bytes


def _content_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class _VisibleResult(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


class ScriptedStatefulPolicy:
    """Deterministic no-cost package used to prove state and resume semantics."""

    def __init__(self, actions: tuple[dict[str, int], ...]) -> None:
        self.actions = actions
        self.reset_calls = 0
        self.reduce_calls = 0
        self.parse_calls = 0
        self.closed = False

    def reset(self, task_instruction: str) -> bytes:
        self.reset_calls += 1
        return canonical_json_bytes(
            {"instruction": task_instruction, "action_index": 0, "history": []}
        )

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        value = json.loads(state)
        index = value["action_index"]
        if index >= len(self.actions):
            raise RuntimeError("scripted policy has no remaining action")
        return {
            "screenshot_digest": "sha256:" + hashlib.sha256(screenshot).hexdigest(),
            "scripted_action": self.actions[index],
        }

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        self.reduce_calls += 1
        value = json.loads(state)
        response = json.loads(canonical_response)
        value["history"].append({"response_digest": _content_digest(response)})
        return canonical_json_bytes(value)

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        value = json.loads(state)
        value["history"].append({"failure_code": failure_code})
        return canonical_json_bytes(value)

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        del canonical_response
        return None

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        self.parse_calls += 1
        del state
        response = json.loads(canonical_response)
        candidate = json.loads(response["content"])
        if not isinstance(candidate, dict):
            raise TypeError("provider content must be one action object")
        return candidate

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        value = json.loads(state)
        value["pending_action_digest"] = _content_digest(candidate)
        return canonical_json_bytes(value)

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: _VisibleResult
    ) -> bytes:
        value = json.loads(state)
        value.pop("pending_action_digest", None)
        value["history"].append(
            {
                "action_digest": _content_digest(action),
                "result_digest": _content_digest(result.to_dict()),
            }
        )
        value["action_index"] += 1
        return canonical_json_bytes(value)

    def close(self) -> None:
        self.closed = True
