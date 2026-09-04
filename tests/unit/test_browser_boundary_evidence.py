from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixelgym.tasks.vendor_form.browser_contract import (
    ChromiumLaunchPath,
    build_chromium_argv,
    chromium_renderer_contract,
)
from pixelgym.validation.audit import validate_reward_hacking
from pixelgym.validation.browser_boundary import (
    BROWSER_BOUNDARY_SCHEMA_VERSION,
    BROWSER_BOUNDARY_VALIDATOR,
    GUEST_BROWSER_BOUNDARY_SCHEMA_VERSION,
    GUEST_BROWSER_BOUNDARY_VALIDATOR,
    GUEST_SOURCE_PATHS,
    SOURCE_PATHS,
    browser_boundary_evidence_passed,
    browser_boundary_source_hashes_match,
    guest_browser_boundary_evidence_passed,
    inspect_navigation_surface,
    source_hashes,
)

_CHECK_NAMES = (
    "submit_response_ok",
    "submit_response_body_exact",
    "visible_validation_state_settled",
    "one_privileged_submission_recorded",
    "privileged_submission_exact",
    "evaluator_observed_submission",
    "evaluator_matched_task_identity",
    "evaluator_rejected_incomplete_submission",
    "derived_environment_reward_zero",
    "evaluator_mismatches_exact",
)


def _evidence(repository_root: Path) -> dict:
    checks = [{"name": name, "passed": True} for name in _CHECK_NAMES]
    return {
        "schema_version": BROWSER_BOUNDARY_SCHEMA_VERSION,
        "validator": BROWSER_BOUNDARY_VALIDATOR,
        "source_sha256": source_hashes(repository_root),
        "browser": {
            "engine": "chromium",
            "version": "test-chromium",
            "args": list(build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)[1:]),
            "renderer_contract": chromium_renderer_contract(),
        },
        "seed": 7,
        "task_id": "vf-test",
        "boundary": {
            "submit_response_status": 200,
            "submit_response_body": {"submission_number": 1},
            "visible_status": "Complete all required fields before submitting.",
            "submission_count": 1,
            "submission": {
                "task_id": "vf-test",
                "seed": 7,
                "values": {
                    "company_name": "",
                    "contact_email": "",
                    "contact_phone": "",
                    "tax_id": "",
                    "country": "",
                    "payment_terms": "",
                    "expedited_onboarding": False,
                },
                "submitted_at_step": 1,
                "final": True,
            },
            "evaluation": {
                "submitted": True,
                "task_id_matches": True,
                "success": False,
                "score": 0.0,
                "mismatched_fields": ["company_name"],
                "derived_environment_reward": 0.0,
            },
        },
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "passed_count": len(checks),
            "failed_count": 0,
            "passed": True,
        },
    }


def _guest_evidence(repository_root: Path) -> dict:
    check_names = (
        "active_window_identified",
        "active_window_is_chromium",
        "active_window_fills_observation",
        "active_window_uses_protected_presentation",
        "observation_shape_exact",
        "provider_closed",
        "renderer_contract_matches_guest_launch",
        "task_app_reaches_top_edge",
    )
    checks = [{"name": name, "passed": True} for name in check_names]
    return {
        "schema_version": GUEST_BROWSER_BOUNDARY_SCHEMA_VERSION,
        "validator": GUEST_BROWSER_BOUNDARY_VALIDATOR,
        "source_sha256": source_hashes(repository_root, GUEST_SOURCE_PATHS),
        "browser_launch": {
            "presentation_mode": "app",
            "presentation_mode_fallback_from": "kiosk",
            "presentation_mode_reason": "kiosk did not preserve the required viewport",
            "renderer_contract": chromium_renderer_contract(),
            "effective_argv": list(build_chromium_argv(ChromiumLaunchPath.OSWORLD_GUEST)),
        },
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "passed_count": len(checks),
            "failed_count": 0,
            "passed": True,
        },
    }


def test_browser_boundary_evidence_requires_every_named_check(tmp_path: Path) -> None:
    for relative in SOURCE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    evidence = _evidence(tmp_path)

    assert browser_boundary_evidence_passed(evidence) is True
    assert browser_boundary_source_hashes_match(evidence, tmp_path) is True

    evidence["checks"][0]["passed"] = False
    evidence["summary"] = {
        "check_count": 10,
        "passed_count": 9,
        "failed_count": 1,
        "passed": False,
    }
    assert browser_boundary_evidence_passed(evidence) is False


def test_browser_boundary_evidence_requires_browser_version(tmp_path: Path) -> None:
    for relative in SOURCE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    evidence = _evidence(tmp_path)
    del evidence["browser"]["version"]

    assert browser_boundary_evidence_passed(evidence) is False


def test_browser_boundary_source_hashes_detect_stale_code(tmp_path: Path) -> None:
    for relative in SOURCE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    evidence = _evidence(tmp_path)

    changed = tmp_path / SOURCE_PATHS[1]
    changed.write_text("changed", encoding="utf-8")

    assert browser_boundary_evidence_passed(evidence) is True
    assert browser_boundary_source_hashes_match(evidence, tmp_path) is False


def test_ordinary_browser_chrome_fixture_fails_navigation_surface_check() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    with Image.open(repository_root / "artifacts/day-2/first-real-reset.png") as image:
        ordinary_chrome = np.asarray(image.convert("RGB").crop((0, 0, 1024, 768)))
    window_state = {
        "active_window_id": "0x100",
        "windows": [
            {
                "id": "0x100",
                "x": 70,
                "y": 27,
                "width": 1850,
                "height": 1053,
                "class": "google-chrome.Google-chrome",
                "title": "Vendor Onboarding - Google Chrome",
            }
        ],
        "active_window_properties": "_NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ",
    }

    result = inspect_navigation_surface(window_state, ordinary_chrome)

    assert result["active_window_fullscreen"] is False
    assert result["checks"]["active_window_fills_observation"] is False
    assert result["checks"]["task_app_reaches_top_edge"] is False


def test_guest_navigation_evidence_requires_every_named_check(tmp_path: Path) -> None:
    for relative in GUEST_SOURCE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    evidence = _guest_evidence(tmp_path)

    assert guest_browser_boundary_evidence_passed(evidence) is True

    evidence["checks"][0]["passed"] = False
    evidence["summary"] = {
        "check_count": 8,
        "passed_count": 7,
        "failed_count": 1,
        "passed": False,
    }
    assert guest_browser_boundary_evidence_passed(evidence) is False


def test_playwright_and_guest_evidence_report_same_renderer_identity(tmp_path: Path) -> None:
    for relative in set(SOURCE_PATHS) | set(GUEST_SOURCE_PATHS):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    playwright = _evidence(tmp_path)
    guest = _guest_evidence(tmp_path)

    assert (
        playwright["browser"]["renderer_contract"]["identity"]
        == guest["browser_launch"]["renderer_contract"]["identity"]
    )


def test_reward_audit_requires_current_browser_boundary_evidence() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))
    evidence = _evidence(repository_root)
    evidence["guest_navigation_surface"] = _guest_evidence(repository_root)

    current = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=evidence,
        real_reset=json.loads((raw / "real-reset.json").read_text(encoding="utf-8")),
        repository_root=repository_root,
    )
    empty_submit = next(
        row for row in current["attacks"] if row["attack"] == "Empty or partial Submit"
    )
    assert empty_submit["evidence_passed"] is True
    assert current["browser_boundary_evidence"]["source_hashes_match"] is True

    evidence["source_sha256"][SOURCE_PATHS[1]] = "0" * 64
    stale = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=evidence,
        real_reset=json.loads((raw / "real-reset.json").read_text(encoding="utf-8")),
        repository_root=repository_root,
    )
    empty_submit = next(
        row for row in stale["attacks"] if row["attack"] == "Empty or partial Submit"
    )
    assert empty_submit["evidence_passed"] is False
    assert stale["summary"]["passed"] is False


def test_reward_audit_rejects_browser_evidence_without_version() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))
    evidence = _evidence(repository_root)
    del evidence["browser"]["version"]

    result = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=evidence,
        repository_root=repository_root,
    )

    empty_submit = next(
        row for row in result["attacks"] if row["attack"] == "Empty or partial Submit"
    )
    assert empty_submit["evidence_passed"] is False
    assert "Chromium None" not in empty_submit["evidence"]
    assert result["summary"]["passed"] is False


def test_reward_audit_fails_closed_without_real_guest_navigation_evidence() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))

    result = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=_evidence(repository_root),
        repository_root=repository_root,
    )

    navigation = next(
        row for row in result["attacks"] if row["attack"] == "Navigate to a completion endpoint"
    )
    reset = next(
        row for row in result["attacks"] if row["attack"] == "Provider reset fails silently"
    )
    assert navigation["evidence_passed"] is False
    assert reset == {
        "attack": "Provider reset fails silently",
        "disposition": "known limitation",
        "evidence": "No passing stored real-reset evidence was supplied to this audit run.",
        "evidence_passed": False,
    }
    assert result["evidence_inputs"]["guest_browser_boundary_passed"] is False
    assert result["evidence_inputs"]["validated_action_snapshot_passed"] is True


def test_reward_audit_requires_repository_root_for_source_verification() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))

    with pytest.raises(TypeError, match="repository_root"):
        validate_reward_hacking(reward, spaces)  # type: ignore[call-arg]
