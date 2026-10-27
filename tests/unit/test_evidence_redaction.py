from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.evidence_redaction import redact_evidence_text, standard_path_replacements


def test_path_redaction_requires_filename_boundaries() -> None:
    replacements = [(Path("/tmp"), "<system-temp>")]

    assert redact_evidence_text("/tmp/run", replacements) == "<system-temp>/run"
    assert redact_evidence_text("file:///tmp/run", replacements) == ("file://<system-temp>/run")
    assert redact_evidence_text("/var/tmp/run", replacements) == "/var/tmp/run"
    assert redact_evidence_text("/tmpfiles/run", replacements) == "/tmpfiles/run"


def test_standard_replacements_redact_pytest_basetemp_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: two D5.10 command records kept ``pytest-of-<username>`` because only
    # the temp directory itself was replaced.
    monkeypatch.setattr("pixelgym.evidence_redaction.tempfile.gettempdir", lambda: "/tmp")
    monkeypatch.setattr("pixelgym.evidence_redaction.getpass.getuser", lambda: "alice")
    replacements = standard_path_replacements([])

    assert redact_evidence_text("/tmp/pytest-of-alice/pytest-3/popen-gw0/t0", replacements) == (
        "<system-temp>/pytest-of-<user>/pytest-3/popen-gw0/t0"
    )
    assert redact_evidence_text("/tmp/other-run", replacements) == "<system-temp>/other-run"


def test_standard_replacements_survive_unknown_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_user() -> str:
        raise OSError("no login name")

    monkeypatch.setattr("pixelgym.evidence_redaction.getpass.getuser", _no_user)

    assert any(label == "<system-temp>" for _path, label in standard_path_replacements([]))


def test_shared_redactor_removes_known_local_demo_secrets() -> None:
    assert redact_evidence_text("token=local_demo_postgres_only", []) == (
        "token=<redacted-local-demo-secret>"
    )
