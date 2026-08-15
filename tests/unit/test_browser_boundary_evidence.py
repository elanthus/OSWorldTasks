from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.validation.audit import validate_reward_hacking
from pixelgym.validation.browser_boundary import (
    BROWSER_BOUNDARY_SCHEMA_VERSION,
    BROWSER_BOUNDARY_VALIDATOR,
    SOURCE_PATHS,
    browser_boundary_evidence_passed,
    browser_boundary_source_hashes_match,
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
        "browser": {"engine": "chromium", "version": "test-chromium"},
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


def test_reward_audit_requires_current_browser_boundary_evidence() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))
    evidence = _evidence(repository_root)

    current = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=evidence,
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
        repository_root=repository_root,
    )
    empty_submit = next(
        row for row in stale["attacks"] if row["attack"] == "Empty or partial Submit"
    )
    assert empty_submit["evidence_passed"] is False
    assert stale["summary"]["passed"] is False


def test_reward_audit_requires_repository_root_for_source_verification() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = repository_root / "artifacts/day-2/raw"
    reward = json.loads((raw / "reward-timing.json").read_text(encoding="utf-8"))
    spaces = json.loads((raw / "space-integrity.json").read_text(encoding="utf-8"))

    with pytest.raises(TypeError, match="repository_root"):
        validate_reward_hacking(reward, spaces)  # type: ignore[call-arg]
