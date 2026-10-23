"""Opt-in, deterministic single-fence action extraction after frozen identity checks."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pixelgym.actions import build_action_space, validate_action
from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.cli_memory_calibration import CliMemoryPolicy, CliMemoryRunner
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    PolicyManifest,
    content_digest,
    sha256_bytes,
)

PARSER_VERSION = "pixelgym-agent-v5-claude-single-fenced-action-parser-v1"
ADAPTER_VERSION = "pixelgym-agent-v5-single-fenced-action-extraction-v1"
FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)


class SingleFencedActionPolicy(claude.ClaudeCodePolicy):
    """Change only action decoding; super.parse validates identity first.

    No substring search for braces, coercion, repair call, retry, or schema repair.
    Receipts contain hashes and character offsets, never response text.
    """

    def __init__(self, *, runtime_identity: claude.ClaudeRuntimeIdentity) -> None:
        super().__init__(runtime_identity=runtime_identity)
        self.on_receipt: Callable[[dict[str, Any]], None] | None = None
        self.last_receipt: dict[str, Any] = {}

    def _decode_action_content(self, content: str) -> Any:
        if not isinstance(content, str):
            raise TypeError("Claude action content must be text")
        # Bare JSON retains the original decoder, including duplicate-key rejection.
        if "```" not in content:
            self.last_receipt["envelope"] = "bare_json"
            return claude._decode_action_content(content)
        if content.count("```") != 2:
            raise ValueError("exactly one complete fence required")
        match = FENCE.search(content)
        if match is None:
            raise ValueError("unsupported or incomplete JSON fence")
        outside = content[: match.start()] + content[match.end() :]
        # Deliberately conservative: reject other objects/arrays or backtick fences,
        # including inline candidate alternatives and nested/longer fence markers.
        if any(character in outside for character in "{}[]`"):
            raise ValueError("ambiguous content outside action fence")
        self.last_receipt.update(
            envelope="single_json_fence",
            extraction_applied=bool(outside.strip()),
            selected_character_span=[match.start(1), match.end(1)],
            selected_content_digest="sha256:" + sha256_bytes(match.group(1).encode()),
        )
        return claude._decode_action_content(match.group(1))

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        self.last_receipt = {
            "adapter_version": ADAPTER_VERSION,
            "parser_version": PARSER_VERSION,
            "canonical_response_digest": "sha256:" + sha256_bytes(canonical_response),
            "decision": "rejected",
            "extraction_applied": False,
        }
        try:
            candidate = super().parse(canonical_response, state)
            validate_action(build_action_space(SCREEN_WIDTH, SCREEN_HEIGHT), candidate)
            self.last_receipt.update(
                decision="accepted", candidate_digest=content_digest(candidate)
            )
            return candidate
        except (ValueError, TypeError, KeyError) as exc:
            self.last_receipt["rejection_type"] = type(exc).__name__
            raise
        finally:
            if self.on_receipt is not None:
                self.on_receipt(dict(self.last_receipt))


def extraction_manifest(root: Path, base: PolicyManifest) -> PolicyManifest:
    sources = {
        path: "sha256:" + sha256_bytes((root / path).read_bytes())
        for path in ("pixelgym/grounding/v5/action_extraction.py", "pixelgym/actions.py")
    }
    fields = {key: value for key, value in vars(base).items() if key != "policy_id"}
    fields.update(
        parser_version=PARSER_VERSION,
        harness_digest=content_digest({"base": base.harness_digest, "extraction_sources": sources}),
        sandbox=replace(
            base.sandbox,
            runtime_digest=content_digest(
                {"base": base.sandbox.runtime_digest, "extraction_sources": sources}
            ),
        ),
    )
    return PolicyManifest.build(**fields)


class ExtractionMemoryRunner(CliMemoryRunner):
    """Journal a per-attempt receipt before the inherited candidate/dispatch boundary."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if (
            self.manifest.parser_version != PARSER_VERSION
            or not isinstance(self.policy, CliMemoryPolicy)
            or not isinstance(self.policy.base, SingleFencedActionPolicy)
        ):
            raise ValueError("extraction policy and frozen parser identity must agree")

    def _act(self, **kwargs: Any) -> dict[str, Any]:
        trial, step = kwargs["trial_id"], kwargs["step_index"]

        def record(receipt: dict[str, Any]) -> None:
            self.journal.append_event(
                event_key=f"{trial}/step-{step:04d}/action_extraction",
                kind="action_extraction",
                trial_id=trial,
                step_index=step,
                payload=receipt,
            )

        base = cast(SingleFencedActionPolicy, cast(CliMemoryPolicy, self.policy).base)
        base.on_receipt = record
        try:
            return super()._act(**kwargs)
        finally:
            base.on_receipt = None
