from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelgym.evidence_redaction import (
    redact_evidence_text,
    repository_relative_evidence_text,
    standard_path_replacements,
)


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
    replacements = standard_path_replacements([])

    assert redact_evidence_text("/tmp/pytest-of-alice/pytest-3/popen-gw0/t0", replacements) == (
        "<system-temp>/pytest-of-<user>/pytest-3/popen-gw0/t0"
    )
    assert redact_evidence_text("/tmp/other-run", replacements) == "<system-temp>/other-run"


def test_pytest_basetemp_redaction_is_user_independent_and_keeps_same_line_text() -> None:
    # The child that produced the output may have run as another user, and the user
    # lookup may be unavailable; neither case may leak the name or eat trailing text.
    replacements = [(Path("/tmp"), "<system-temp>")]

    assert redact_evidence_text("/tmp/pytest-of-bob failed to initialize", replacements) == (
        "<system-temp>/pytest-of-<user> failed to initialize"
    )
    assert redact_evidence_text("'/tmp/pytest-of-c.d-e/pytest-1' x", replacements) == (
        "'<system-temp>/pytest-of-<user>/pytest-1' x"
    )
    assert redact_evidence_text("/tmp/pytest-offline", replacements) == (
        "<system-temp>/pytest-offline"
    )


def test_shared_redactor_removes_known_local_demo_secrets() -> None:
    assert redact_evidence_text("token=local_demo_postgres_only", []) == (
        "token=<redacted-local-demo-secret>"
    )


def test_paths_under_the_repository_are_stored_repository_relative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a golden replay started with an absolute --output-dir stored the
    # operator's checkout path in every frame path of its episode file.
    monkeypatch.setattr("pixelgym.evidence_redaction.Path.home", lambda: Path("/home/alice"))
    monkeypatch.setattr("pixelgym.evidence_redaction.tempfile.gettempdir", lambda: "/tmp")
    root = Path("/home/alice/dev/repo")

    def stored(value: str) -> str:
        return repository_relative_evidence_text(value, root)

    assert stored('"/home/alice/dev/repo/artifacts/run/frames/step-001.png"') == (
        '"artifacts/run/frames/step-001.png"'
    )
    assert stored('File "/home/alice/dev/repo/scripts/driver.py", line 3') == (
        'File "scripts/driver.py", line 3'
    )
    # Already relative: unchanged, so a relative and an absolute invocation store one value.
    assert stored('"artifacts/run/frames/step-001.png"') == '"artifacts/run/frames/step-001.png"'


def test_paths_outside_the_repository_keep_no_local_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pixelgym.evidence_redaction.Path.home", lambda: Path("/home/alice"))
    monkeypatch.setattr("pixelgym.evidence_redaction.tempfile.gettempdir", lambda: "/tmp")
    root = Path("/home/alice/dev/repo")

    def stored(value: str) -> str:
        return repository_relative_evidence_text(value, root)

    # A path with no repository-relative form is labelled, never left absolute and local.
    assert stored("/home/alice/images/guest.qcow2") == "<home>/images/guest.qcow2"
    assert stored("/tmp/run/out.json") == "<system-temp>/run/out.json"
    # The root itself has no relative form either; it stays labelled.
    assert stored("cwd=/home/alice/dev/repo") == "cwd=<repo>"
    # A sibling directory that only shares the root's spelling is not inside it.
    assert stored("/home/alice/dev/repo-old/a.png") == "<home>/dev/repo-old/a.png"


def test_a_directory_json_escapes_is_still_rewritten_in_dumped_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The writers dump JSON and then rewrite the text, where a non-ASCII directory name
    # appears only in its escaped spelling.
    monkeypatch.setattr("pixelgym.evidence_redaction.Path.home", lambda: Path("/home/jürgen"))
    monkeypatch.setattr("pixelgym.evidence_redaction.tempfile.gettempdir", lambda: "/tmp")
    root = Path("/home/jürgen/dev/repo")
    dumped = json.dumps(
        {"inside": "/home/jürgen/dev/repo/artifacts/a.png", "outside": "/home/jürgen/guest.qcow2"}
    )
    assert "jürgen" not in dumped and "j\\u00fcrgen" in dumped

    assert json.loads(repository_relative_evidence_text(dumped, root)) == {
        "inside": "artifacts/a.png",
        "outside": "<home>/guest.qcow2",
    }


def test_a_url_under_the_repository_keeps_the_repository_label() -> None:
    # Dropping the root from file:///root/repo/app/x would leave file://app/x, a different URL.
    root = Path("/srv/checkout/repo")

    assert repository_relative_evidence_text("file:///srv/checkout/repo/app/x", root) == (
        "file://<repo>/app/x"
    )
