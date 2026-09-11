from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import pixelgym.grounding.verification as grounding_verification
from pixelgym.grounding.report import render_report_markdown
from pixelgym.grounding.verification import (
    GroundingVerificationError,
    verify_grounding_report,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOP_LEVEL_EVIDENCE = (
    "grounding-report-provenance-v1.json",
    "grounding-report.md",
    "grounding-v3-haiku-gemini-report.md",
    "grounding-results.json",
    "grounding-protocol.md",
    "grounding-dataset.jsonl",
    "grounding-overlays.jsonl",
    "grounding-predictions.jsonl",
    "grounding-error-review.jsonl",
    "grounding-error-review-decisions.json",
)


def _digest_verification_surface(repository_root: Path) -> dict[str, str]:
    artifacts = repository_root / "artifacts"
    paths = [artifacts / name for name in TOP_LEVEL_EVIDENCE]
    paths.extend(path for path in (artifacts / "grounding").rglob("*") if path.is_file())
    return {
        path.relative_to(repository_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def _copy_verification_fixture(tmp_path: Path) -> Path:
    repository_root = tmp_path / "repository"
    artifacts = repository_root / "artifacts"
    artifacts.mkdir(parents=True)
    for name in TOP_LEVEL_EVIDENCE:
        shutil.copy2(REPOSITORY_ROOT / "artifacts" / name, artifacts / name)
    shutil.copytree(REPOSITORY_ROOT / "artifacts/grounding", artifacts / "grounding")
    return repository_root


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _update_recorded_digest(
    provenance_path: Path,
    *,
    record_name: str,
    artifact_path: Path,
) -> str:
    provenance = json.loads(provenance_path.read_text())
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    record = provenance["canonical"][record_name]
    record["sha256"] = digest
    record["size_bytes"] = artifact_path.stat().st_size
    _write_json(provenance_path, provenance)
    return digest


def test_canonical_grounding_report_verifies_without_mutating_artifacts() -> None:
    before = _digest_verification_surface(REPOSITORY_ROOT)

    result = verify_grounding_report(REPOSITORY_ROOT)

    assert result["status"] == "verified"
    assert result["experiment_id"] == "pixelgym-grounding-v1-gpt-5.4-mini-2026-08-10"
    assert result["headline"]["raw_correct_count"] == 56
    assert result["headline"]["marks_correct_count"] == 100
    assert result["headline"]["paired_delta_percentage_points"] == 44.0
    assert result["wrote_files"] is False
    assert _digest_verification_surface(REPOSITORY_ROOT) == before


def test_verifier_recomputes_proposal_coverage_after_digest_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _copy_verification_fixture(tmp_path)
    artifacts = repository_root / "artifacts"
    results_path = artifacts / "grounding-results.json"
    provenance_path = artifacts / "grounding-report-provenance-v1.json"
    results = json.loads(results_path.read_text())
    results["set_of_marks"]["proposal_coverage"] = 0.99
    _write_json(results_path, results)
    digest = _update_recorded_digest(
        provenance_path,
        record_name="results",
        artifact_path=results_path,
    )
    monkeypatch.setattr(grounding_verification, "CANONICAL_RESULTS_SHA256", digest)

    with pytest.raises(GroundingVerificationError, match="recomputed results.set_of_marks"):
        verify_grounding_report(repository_root)


def test_report_content_mismatch_has_localized_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _copy_verification_fixture(tmp_path)
    artifacts = repository_root / "artifacts"
    report_path = artifacts / "grounding-report.md"
    provenance_path = artifacts / "grounding-report-provenance-v1.json"
    report_path.write_text(report_path.read_text() + "\nUnexpected verifier-only line.\n")
    digest = _update_recorded_digest(
        provenance_path,
        record_name="report",
        artifact_path=report_path,
    )
    monkeypatch.setattr(grounding_verification, "CANONICAL_REPORT_SHA256", digest)

    with pytest.raises(GroundingVerificationError) as error:
        verify_grounding_report(repository_root)

    message = str(error.value)
    assert "canonical report content mismatch" in message
    assert "--- expected" in message
    assert "+++ actual" in message
    assert "+Unexpected verifier-only line." in message
    assert len(message) < 1_000


def test_report_render_uses_protocol_from_results() -> None:
    results = json.loads((REPOSITORY_ROOT / "artifacts/grounding-results.json").read_text())
    gallery = json.loads(
        (REPOSITORY_ROOT / "artifacts/grounding/gallery/manifest.json").read_text()
    )
    results["protocol_version"] = "frozen-protocol-version"

    report = render_report_markdown(results, gallery)

    assert "- Protocol: `frozen-protocol-version`" in report


def test_verifier_rejects_identity_sample_headline_and_missing_evidence(
    tmp_path: Path,
) -> None:
    repository_root = _copy_verification_fixture(tmp_path)
    artifacts = repository_root / "artifacts"
    provenance_path = artifacts / "grounding-report-provenance-v1.json"
    report_path = artifacts / "grounding-report.md"
    predictions_path = artifacts / "grounding-predictions.jsonl"
    original_provenance = provenance_path.read_bytes()
    original_report = report_path.read_bytes()
    original_predictions = predictions_path.read_bytes()

    provenance = json.loads(original_provenance)
    provenance["canonical"]["identity"]["model"] = "claude-haiku-4-5"
    _write_json(provenance_path, provenance)
    with pytest.raises(GroundingVerificationError, match="experiment identity"):
        verify_grounding_report(repository_root)

    provenance_path.write_bytes(original_provenance)
    report_path.write_text(report_path.read_text().replace("100 examples", "99 examples", 1))
    with pytest.raises(GroundingVerificationError, match="sample size"):
        verify_grounding_report(repository_root)

    report_path.write_bytes(original_report)
    report_path.write_text(report_path.read_text().replace("56/100", "55/100", 1))
    with pytest.raises(GroundingVerificationError, match="headline statistics"):
        verify_grounding_report(repository_root)

    report_path.write_bytes(original_report)
    predictions_path.unlink()
    with pytest.raises(GroundingVerificationError, match="missing evidence file"):
        verify_grounding_report(repository_root)

    predictions_path.write_bytes(original_predictions)
    assert verify_grounding_report(repository_root)["status"] == "verified"
