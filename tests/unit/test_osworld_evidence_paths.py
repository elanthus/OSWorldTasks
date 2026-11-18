"""OSWorld evidence writers store repository-relative paths however they are invoked.

The golden replay recorder stored ``str(path)`` for each frame, so a run started with an
absolute ``--output-dir`` wrote the operator's checkout path into its episode file. These
tests run the writers with absolute paths and read back what was stored.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pixelgym.backends.fake import FakeBackend
from scripts import osworld_golden_trajectory as recorder
from scripts import osworld_guest_check as guest_check
from scripts import smoke_osworld_reset as smoke

FIXTURE = Path(__file__).resolve().parents[1] / "integration/fixtures"


class _Backend(FakeBackend):
    """The fake form behind the two members the recorder reads from the OSWorld backend."""

    def __init__(self, config: Any) -> None:
        super().__init__()
        self._config = config

    @property
    def last_submissions(self) -> Any:
        return self.read_submissions()

    def integration_metadata(self) -> dict[str, Any]:
        # As the real backend does: the configured path, exactly as the caller spelled it.
        return {"guest_image_path": str(self._config.guest_image_path)}


def _path_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture_path": evidence["fixture_path"],
        "contact_sheet_path": evidence["contact_sheet_path"],
        "guest_image_path": evidence["backend_metadata"]["guest_image_path"],
        "screenshot_paths": [step["screenshot_path"] for step in evidence["trace"]],
    }


def test_golden_recorder_stores_relative_paths_for_an_absolute_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(FIXTURE / "osworld_golden_trajectory_seed7.json", repo / "fixture.json")
    monkeypatch.setattr(recorder, "REPO_ROOT", repo)
    monkeypatch.setattr(recorder, "OSWorldBackend", _Backend)

    returned = recorder.record_real(
        repo / "fixture.json", repo / ".cache" / "guest.qcow2", repo / "out" / "real-golden"
    )

    text = (repo / "out" / "raw" / "real-golden-episode.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and str(Path.home()) not in text
    stored = json.loads(text)
    assert returned == stored and stored["summary"]["passed"] is True
    assert _path_fields(stored) == {
        "fixture_path": "fixture.json",
        "contact_sheet_path": "out/real-golden/contact-sheet.png",
        "guest_image_path": ".cache/guest.qcow2",
        "screenshot_paths": [
            f"out/real-golden/frames/step-{step:03d}.png" for step in range(1, 113)
        ],
    }
    # Every stored path names a file the run wrote, read from the repository root.
    assert all((repo / path).is_file() for path in _path_fields(stored)["screenshot_paths"])
    assert (repo / stored["contact_sheet_path"]).is_file()


def test_guest_check_results_store_relative_paths(tmp_path: Path) -> None:
    root = guest_check.REPO_ROOT
    results = {
        "provenance": {
            "guest_image_path": str(root / ".cache/osworld/guest.qcow2"),
            "pixelgym_module_path": str(root / "pixelgym/__init__.py"),
        },
        "golden": {"error": f'  File "{root}/scripts/osworld_golden_trajectory.py", line 1\n'},
        "elsewhere": str(tmp_path / "guest.qcow2"),
    }

    guest_check._write(tmp_path / "results.json", results)

    text = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert str(root) not in text and str(tmp_path) not in text
    stored = json.loads(text)
    assert stored["provenance"] == {
        "guest_image_path": ".cache/osworld/guest.qcow2",
        "pixelgym_module_path": "pixelgym/__init__.py",
    }
    assert stored["golden"]["error"] == ('  File "scripts/osworld_golden_trajectory.py", line 1\n')
    # Outside the repository there is no relative form; the directory is labelled instead.
    assert stored["elsewhere"].startswith("<") and stored["elsewhere"].endswith("/guest.qcow2")


def test_smoke_reset_evidence_stores_relative_paths() -> None:
    root = smoke.REPO_ROOT
    evidence = {
        "screenshot_path": str(root / "artifacts/day-2/first-real-reset.png"),
        "backend_metadata": {"guest_image_path": str(root / ".cache/osworld/guest.qcow2")},
        "error": {"traceback": f'  File "{root}/pixelgym/backends/osworld.py", line 1\n'},
    }

    text = smoke.evidence_text(evidence)

    assert str(root) not in text
    assert json.loads(text) == {
        "screenshot_path": "artifacts/day-2/first-real-reset.png",
        "backend_metadata": {"guest_image_path": ".cache/osworld/guest.qcow2"},
        "error": {"traceback": '  File "pixelgym/backends/osworld.py", line 1\n'},
    }
