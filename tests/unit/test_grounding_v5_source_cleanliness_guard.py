"""Policy manifests and admission reject tracked source changes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pixelgym.grounding.v5.panel_policy import GEMINI_STATEFUL
from pixelgym.grounding.v5.screenshot_memory import (
    build_screenshot_policy_manifest,
)
from scripts import prepare_grounding_v5_memory as admission

ROOT = Path(__file__).resolve().parents[2]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def manifest_checkout(tmp_path: Path) -> Path:
    # Real local Git state; no network, provider, or dependency installation.
    root = tmp_path / "source"
    root.mkdir()
    for name in ("panel_policy.py", "openrouter_policy.py", "runner.py", "screenshot_memory.py"):
        relative = Path("pixelgym/grounding/v5") / name
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)
    for relative in (Path("pyproject.toml"), Path("requirements/platform-py312.lock")):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    return root


@pytest.mark.parametrize("staged", [True, False])
def test_dirty_source_rejected_by_manifest_and_admission(
    manifest_checkout: Path, monkeypatch: pytest.MonkeyPatch, staged: bool
) -> None:
    root = manifest_checkout
    path = root / "pixelgym/grounding/v5/screenshot_memory.py"
    path.write_text(path.read_text() + "\n# tracked edit\n")
    if staged:
        git(root, "add", str(path.relative_to(root)))
    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        build_screenshot_policy_manifest(
            root, config=GEMINI_STATEFUL, code_revision="fixture", retain_screenshots=True
        )
    monkeypatch.setattr(admission, "ROOT", root)
    monkeypatch.setattr(admission, "OUTPUT", root / "new-evidence")
    # A missing source file would fail hashing first if the guard were too late.
    monkeypatch.setattr(admission, "SOURCE_PATHS", ("missing-file",))
    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        admission.build()
    assert not (root / "new-evidence").exists()


def test_clean_source_and_untracked_output_are_allowed(manifest_checkout: Path) -> None:
    root = manifest_checkout
    (root / "untracked-output.json").write_text("{}")
    manifest = build_screenshot_policy_manifest(
        root, config=GEMINI_STATEFUL, code_revision="fixture", retain_screenshots=True
    )
    assert manifest.dirty_worktree_policy == "reject-tracked-changes"
