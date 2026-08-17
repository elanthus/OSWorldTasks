from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.generate_d412_evidence_report import CHECKLIST, generate

REVISION = "f92e307af7a3830347d50ca63f6a7d481489935c"


def test_generator_indexes_stored_observations_without_a_gate_verdict(
    repository_root: Path,
) -> None:
    evidence_dir = repository_root / "artifacts/platform/d4.12" / REVISION

    manifest = generate(repository_root, evidence_dir)
    report = (evidence_dir / "REPORT.md").read_text()

    assert manifest["verdict"] is None
    assert manifest["verdict_owner"] == "project owner"
    assert manifest["evidence_revision"] == REVISION
    assert len(manifest["checklist_items"]) == len(CHECKLIST) == 16
    assert all(not item["missing_evidence"] for item in manifest["checklist_items"])
    assert "- [ ]" not in report
    assert "- [x]" not in report.lower()
    assert "declare a milestone verdict" in report


def test_generated_artifact_digests_verify_independently(repository_root: Path) -> None:
    evidence_dir = repository_root / "artifacts/platform/d4.12" / REVISION
    manifest = generate(repository_root, evidence_dir)

    for entry in manifest["command_records"]:
        data = (evidence_dir / entry["path"]).read_bytes()
        assert len(data) == entry.get("size", len(data))
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    for entry in manifest["supporting_artifacts"]:
        data = (repository_root / entry["path"]).read_bytes()
        assert len(data) == entry["size"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]

    on_disk = json.loads((evidence_dir / "evidence-manifest.json").read_text())
    assert on_disk["identity_reconciliation"]["stored_counts"] == {
        "api_exchanges": 5,
        "approval_events": 2,
        "audit_events": 11,
        "deployment_events": 3,
        "gate_reports": 3,
        "immutable_failures": 0,
        "immutable_verified": 310,
        "manifest_artifact_union": 310,
        "mlflow_lineage_records": 3,
        "run_manifests": 3,
        "screenshots": 10,
    }
