"""Evidence paths must not carry the operator's filesystem into published artifacts.

The D5.6 Gemini calibration recorded ``str(output_directory / "summary.json")`` for a run
invoked with an absolute path, so the stored plan and summary embedded
``/Users/<operator>/...``. That breaks the repository's no-local-absolute-paths invariant
and makes the recorded value differ between machines for the same run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.grounding.v5.evidence import repository_relative_path


def test_absolute_path_inside_the_repository_is_recorded_relative(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = root / "artifacts" / "run" / "summary.json"
    evidence.parent.mkdir(parents=True)

    recorded = repository_relative_path(root, evidence)

    assert recorded == "artifacts/run/summary.json"
    assert str(tmp_path) not in recorded


def test_recorded_value_does_not_depend_on_how_the_run_was_invoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    evidence = root / "artifacts" / "run" / "attempts.sqlite"
    evidence.parent.mkdir(parents=True)
    monkeypatch.chdir(root)

    absolute_invocation = repository_relative_path(root, evidence)
    relative_invocation = repository_relative_path(root, Path("artifacts/run/attempts.sqlite"))

    assert absolute_invocation == relative_invocation == "artifacts/run/attempts.sqlite"


def test_path_outside_the_repository_falls_back_to_a_resolved_path(tmp_path: Path) -> None:
    # Documented fallback: an out-of-repository evidence path has no portable form. This is
    # unreachable for published evidence, which is always written inside the repository.
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "summary.json"
    outside.parent.mkdir(parents=True)

    assert repository_relative_path(root, outside) == str(outside.resolve())


def test_posix_separators_are_used_so_the_value_is_platform_stable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = root / "artifacts" / "nested" / "run" / "summary.json"
    evidence.parent.mkdir(parents=True)

    assert "\\" not in repository_relative_path(root, evidence)
