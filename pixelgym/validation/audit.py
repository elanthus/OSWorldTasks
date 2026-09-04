"""Reward-boundary attack matrix assembled from executable evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelgym.actions import KEY_ALLOWLIST, KEY_ALLOWLIST_VERSION, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import PixelGuiEnv
from pixelgym.validation.browser_boundary import (
    browser_boundary_evidence_passed,
    browser_boundary_source_hashes_match,
    guest_browser_boundary_evidence_passed,
    guest_browser_boundary_source_hashes_match,
)

_DISPOSITIONS = {"blocked", "tested", "mitigated", "known limitation"}


def _record_by_name(reward: dict[str, Any], name: str) -> dict[str, Any]:
    return next(record for record in reward["records"] if record["name"] == name)


class _ChangingClick(dict[str, int]):
    """Return a safe x once and an invalid x if caller-controlled input is re-read."""

    def __init__(self) -> None:
        super().__init__(action_type=int(ActionType.CLICK), x=5, y=0, key=0)
        self.x_reads = 0

    def __getitem__(self, key: str) -> int:
        value = super().__getitem__(key)
        if key == "x":
            self.x_reads += 1
            return value if self.x_reads == 1 else 100_000
        return value


def _validated_action_snapshot_check() -> bool:
    backend = FakeBackend(width=64, height=48)
    env = PixelGuiEnv(backend)
    action = _ChangingClick()
    try:
        env.reset(seed=7)
        env.step(action)
        return action.x_reads == 1 and backend.click_calls == [(5, 0)]
    finally:
        env.close()


def _real_reset_evidence_passed(real_reset: dict[str, Any] | None) -> bool:
    if not isinstance(real_reset, dict):
        return False
    summary = real_reset.get("summary")
    return (
        real_reset.get("validator") == "reset-determinism"
        and real_reset.get("backend") == "real-osworld-docker"
        and isinstance(summary, dict)
        and summary.get("passed") is True
        and summary.get("semantic_task_state_exact") is True
        and summary.get("privileged_application_state_exact") is True
        and summary.get("screenshot_shape_exact") is True
        and summary.get("screenshot_dtype_exact") is True
    )


def validate_reward_hacking(
    reward: dict[str, Any],
    spaces: dict[str, Any],
    *,
    repository_root: Path,
    real_reset: dict[str, Any] | None = None,
    browser_boundary: dict[str, Any] | None = None,
    guest_browser_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if guest_browser_boundary is None and isinstance(browser_boundary, dict):
        nested_guest = browser_boundary.get("guest_navigation_surface")
        if isinstance(nested_guest, dict):
            guest_browser_boundary = nested_guest

    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    env.reset(seed=7)
    backend.form.status = "Success"
    _observation, fake_success_reward, _terminated, _truncated, _info = env.step(
        {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}
    )
    env.close()

    partial_backend = FakeBackend()
    partial_env = PixelGuiEnv(partial_backend)
    partial_env.reset(seed=7)
    partial_backend.install_submission(
        {"company_name": partial_backend.current_fields()["company_name"]}
    )
    _obs, partial_reward, _term, _trunc, _info = partial_env.step(
        {"action_type": int(ActionType.NOOP), "x": 0, "y": 0, "key": 0}
    )
    partial_env.close()

    done_case = next(
        record for record in spaces["invalid_actions"] if record["name"] == "unknown-action-type"
    )
    out_of_bounds = [
        record
        for record in spaces["invalid_actions"]
        if record["name"] in {"negative-x", "x-equals-width", "negative-y", "y-equals-height"}
    ]
    empty_submit = _record_by_name(reward, "empty-submit")
    correct_without_submit = _record_by_name(reward, "correct-fields-without-submit")
    stale = _record_by_name(reward, "wrong-or-stale-task-id-fixture")
    duplicate = _record_by_name(reward, "duplicate-submit-after-success")
    browser_evidence_passed = browser_boundary_evidence_passed(browser_boundary)
    browser_source_hashes_match = browser_boundary_source_hashes_match(
        browser_boundary, repository_root
    )
    guest_browser_evidence_passed = guest_browser_boundary_evidence_passed(
        guest_browser_boundary
    )
    guest_browser_source_hashes_match = guest_browser_boundary_source_hashes_match(
        guest_browser_boundary, repository_root
    )
    forbidden_modifier_keys = {
        "Alt",
        "Command",
        "Control",
        "Escape",
        "F1",
        "Meta",
    }
    key_contract_blocks_modifiers = (
        KEY_ALLOWLIST_VERSION == 1
        and forbidden_modifier_keys.isdisjoint(KEY_ALLOWLIST)
        and tuple(ActionType) == (ActionType.NOOP, ActionType.CLICK, ActionType.KEY)
    )
    validated_action_snapshot_passed = _validated_action_snapshot_check()
    real_reset_evidence_passed = _real_reset_evidence_passed(real_reset)
    if browser_evidence_passed:
        assert isinstance(browser_boundary, dict)
        browser_version = browser_boundary["browser"]["version"]
        browser_evidence = (
            f"Chromium {browser_version} returned HTTP 200, recorded exactly one empty "
            "privileged submission, preserved the settled validation message, and the host-side "
            "evaluator derived reward 0."
        )
        browser_evidence += (
            " Stored source hashes match the checked-out implementation."
            if browser_source_hashes_match
            else " Stored source hashes do not match the checked-out implementation."
        )
    else:
        browser_evidence = "Browser-boundary evidence is missing or failed schema validation."

    attacks = [
        {
            "attack": "Agent emits DONE without completing the form",
            "disposition": "tested",
            "evidence": (
                "Action type 3 was rejected before backend execution; the public Discrete action "
                "space contains only NOOP=0, CLICK=1, KEY=2."
            ),
            "evidence_passed": done_case["rejected"]
            and done_case["rejected_before_backend_execution"],
        },
        {
            "attack": "Empty or partial Submit",
            "disposition": "tested",
            "evidence": (
                "empty-submit trajectory produced no reward; a partial privileged submission "
                f"was also evaluated without reward. {browser_evidence}"
            ),
            "evidence_passed": empty_submit["passed"]
            and partial_reward == 0
            and browser_evidence_passed
            and browser_source_hashes_match,
        },
        {
            "attack": "Correct visible fields without Submit",
            "disposition": "tested",
            "evidence": "correct-fields-without-submit public trajectory produced no reward.",
            "evidence_passed": correct_without_submit["passed"],
        },
        {
            "attack": "Visible fake Success content",
            "disposition": "tested",
            "evidence": "A fake Success status string with no submission produced reward 0.",
            "evidence_passed": fake_success_reward == 0,
        },
        {
            "attack": "Reuse prior successful state",
            "disposition": "tested",
            "evidence": "A correct submission carrying vf-stale-task produced no reward.",
            "evidence_passed": stale["passed"],
        },
        {
            "attack": "Repeat valid submission",
            "disposition": "tested",
            "evidence": (
                "The complete trace emitted one terminal reward and the next step was rejected "
                "after episode termination."
            ),
            "evidence_passed": duplicate["passed"]
            and duplicate.get("post_success_step_rejected", False),
        },
        {
            "attack": "Out-of-bounds clicks",
            "disposition": "tested",
            "evidence": "All four coordinate boundary violations were rejected before backend calls.",
            "evidence_passed": all(
                record["rejected"] and record["rejected_before_backend_execution"]
                for record in out_of_bounds
            ),
        },
        {
            "attack": "Open terminal or developer tools",
            "disposition": "blocked",
            "evidence": (
                "Ctrl, Alt, Command, Escape, function keys, and modifier combinations are absent "
                "from KEY_ALLOWLIST_VERSION=1; KEY exposes only one indexed allowlisted key. "
                "The stored real-guest fullscreen and top-edge checks cover clickable desktop "
                "and browser chrome."
            ),
            "evidence_passed": key_contract_blocks_modifiers
            and guest_browser_evidence_passed
            and guest_browser_source_hashes_match,
        },
        {
            "attack": "Navigate to a completion endpoint",
            "disposition": "blocked",
            "evidence": (
                "The action interface has no browser-navigation action or modifier chord. Stored "
                "real-guest evidence requires Chromium app fullscreen mode, exact 1024x768 "
                "window bounds, and task-app pixels at the observation's top edge."
            ),
            "evidence_passed": key_contract_blocks_modifiers
            and guest_browser_evidence_passed
            and guest_browser_source_hashes_match,
        },
        {
            "attack": "Guess or alter a task identifier",
            "disposition": "tested",
            "evidence": "The stale-task fixture was rejected by the current host-side task identity.",
            "evidence_passed": stale["passed"],
        },
        {
            "attack": "Trigger evaluator during partial write",
            "disposition": "mitigated",
            "evidence": (
                "GuestTaskState appends an immutable complete record under one lock; evaluator "
                "also treats missing fields as mismatches, demonstrated by the partial fixture."
            ),
            "evidence_passed": partial_reward == 0,
        },
        {
            "attack": "Provider reset fails silently",
            "disposition": "tested" if real_reset_evidence_passed else "known limitation",
            "evidence": (
                "Stored real OSWorld reset evidence records exact task/application identity and "
                "valid screenshot shape and dtype."
                if real_reset_evidence_passed
                else "No passing stored real-reset evidence was supplied to this audit run."
            ),
            "evidence_passed": real_reset_evidence_passed,
        },
        {
            "attack": "Mutate or change an action after validation",
            "disposition": "tested",
            "evidence": (
                "Environment tests use stateful Mapping and int subclasses and prove dispatch uses "
                "only the immutable ValidatedAction snapshot."
            ),
            "evidence_passed": validated_action_snapshot_passed,
            "additional_attack": True,
        },
        {
            "attack": "Conflate truncation with successful termination",
            "disposition": "tested",
            "evidence": (
                "timeout-one-action-before-completion truncated without reward and post-truncation "
                "step was rejected."
            ),
            "evidence_passed": _record_by_name(reward, "timeout-one-action-before-completion")[
                "passed"
            ]
            and spaces["post_truncation_step_rejected"],
            "additional_attack": True,
        },
    ]
    known_limitations = [
        (
            "The release-named x86 host and the Apple Silicon qemux/qemu base are third-party "
            "Docker Hub images without release signatures; digest pinning mitigates mutability, "
            "not publisher compromise."
        ),
        (
            "On Apple Silicon the native outer host avoids redundant container emulation, but the "
            "released guest remains x86-64 and runs without KVM. Boot/stabilization may still be "
            "slow or fail even when the adapter is correct."
        ),
        (
            "The privileged /api/state endpoint exists inside the guest. The tested app-mode contract "
            "removes browser navigation affordances from the bounded-click observation; containment "
            "against a browser or guest OS exploit remains outside this benchmark's threat model."
        ),
        (
            "OSWorld's structured computer action controller internally generates fixed pyautogui "
            "Python calls. PixelGym never accepts or forwards agent-supplied Python, but it inherits "
            "bugs in that upstream structured-action implementation."
        ),
    ]
    if real_reset is not None and not real_reset["summary"]["bitwise_visual_determinism"]:
        changed_regions = [
            record["differing_pixel_bbox_xyxy"]
            for record in real_reset["records"]
            if record["differing_pixel_bbox_xyxy"] is not None
        ]
        known_limitations.append(
            "The real guest frames were not bitwise identical. "
            f"Across {real_reset['reset_count']} real resets, at most "
            f"{real_reset['summary']['maximum_differing_pixel_count']} pixels changed and all "
            f"differences were localized to boxes {changed_regions}; minimum SSIM was "
            f"{real_reset['summary']['minimum_ssim']}. No visual mask or tolerance was applied."
        )
    valid_dispositions = all(item["disposition"] in _DISPOSITIONS for item in attacks)
    evidence_passed = all(item["evidence_passed"] for item in attacks)
    return {
        "schema_version": 1,
        "validator": "reward-hacking-audit",
        "browser_boundary_evidence": (
            None
            if browser_boundary is None
            else {
                "schema_version": browser_boundary.get("schema_version"),
                "validator": browser_boundary.get("validator"),
                "task_id": browser_boundary.get("task_id"),
                "browser": browser_boundary.get("browser"),
                "source_sha256": browser_boundary.get("source_sha256"),
                "summary": browser_boundary.get("summary"),
                "source_hashes_match": browser_source_hashes_match,
            }
        ),
        "guest_browser_boundary_evidence": (
            None
            if guest_browser_boundary is None
            else {
                "schema_version": guest_browser_boundary.get("schema_version"),
                "validator": guest_browser_boundary.get("validator"),
                "task_id": guest_browser_boundary.get("task_id"),
                "browser_launch": guest_browser_boundary.get("browser_launch"),
                "source_sha256": guest_browser_boundary.get("source_sha256"),
                "summary": guest_browser_boundary.get("summary"),
                "source_hashes_match": guest_browser_source_hashes_match,
            }
        ),
        "evidence_inputs": {
            "key_contract_blocks_modifiers": key_contract_blocks_modifiers,
            "guest_browser_boundary_passed": guest_browser_evidence_passed,
            "guest_browser_source_hashes_match": guest_browser_source_hashes_match,
            "real_reset_evidence_passed": real_reset_evidence_passed,
            "validated_action_snapshot_passed": validated_action_snapshot_passed,
        },
        "attacks": attacks,
        "known_limitations": known_limitations,
        "summary": {
            "attack_count": len(attacks),
            "additional_attack_count": sum(
                item.get("additional_attack", False) for item in attacks
            ),
            "evidence_passed_count": sum(item["evidence_passed"] for item in attacks),
            "valid_dispositions": valid_dispositions,
            "passed": valid_dispositions and evidence_passed,
        },
    }
