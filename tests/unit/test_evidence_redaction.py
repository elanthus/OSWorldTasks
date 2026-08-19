from __future__ import annotations

from pathlib import Path

from pixelgym.evidence_redaction import redact_evidence_text


def test_path_redaction_requires_filename_boundaries() -> None:
    replacements = [(Path("/tmp"), "<system-temp>")]

    assert redact_evidence_text("/tmp/run", replacements) == "<system-temp>/run"
    assert redact_evidence_text("file:///tmp/run", replacements) == (
        "file://<system-temp>/run"
    )
    assert redact_evidence_text("/var/tmp/run", replacements) == "/var/tmp/run"
    assert redact_evidence_text("/tmpfiles/run", replacements) == "/tmpfiles/run"


def test_shared_redactor_removes_known_local_demo_secrets() -> None:
    assert redact_evidence_text("token=local_demo_postgres_only", []) == (
        "token=<redacted-local-demo-secret>"
    )
