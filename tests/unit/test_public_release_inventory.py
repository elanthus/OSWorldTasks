from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

FIXTURE = Path(__file__).parent / "fixtures" / "public_release"
REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "inventory_public_release.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("inventory_public_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_detects_public_release_regressions(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE, root)
    (root / ".gitignore").write_text("ignored/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "add", "README.md", "evidence.json", "LICENSE", "NOTICE", ".gitignore"],
        cwd=root,
        check=True,
    )

    clean = module.build_inventory(root)
    assert clean["summary"]["passed"] is True
    assert clean["links"]["local_link_count"] == 2
    assert clean["license_inventory"]["passed"] is True
    assert clean["redaction_and_asset_inventory"]["review_required_count"] == 0

    readme = root / "README.md"
    ignored = root / "ignored" / "result.json"
    ignored.parent.mkdir()
    ignored.write_text("{}\n")
    readme.write_text(readme.read_text() + "\n[Ignored private evidence](ignored/result.json)\n")
    sensitive = "/" + "Users" + "/alice/private-run\n" + "ghp_" + "A" * 36
    (root / "release-notes.txt").write_text(sensitive)
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "accidental.txt").write_text("person" + "@real-domain.com\n")
    task_dir = root / "pixelgym" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "accidental.txt").write_text("person" + "@another-real-domain.com\n")
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "new-predictions.jsonl").write_text(
        '{"raw_' + 'response":"must be reviewed"}\n'
    )

    regressed = module.build_inventory(root)
    assert regressed["links"]["failure_count"] == 1
    assert regressed["redaction_and_asset_inventory"]["review_required_count"] == 6
    categories = regressed["redaction_and_asset_inventory"]["categories"]
    assert categories["private_paths"]["review_required_count"] == 1
    assert categories["usernames"]["review_required_count"] == 1
    assert categories["credential_or_token_shapes"]["review_required_count"] == 1
    assert categories["email_addresses"]["review_required_count"] == 2
    assert categories["raw_provider_payloads"]["review_required_count"] == 1


def test_history_inventory_finds_sensitive_values_removed_from_head(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture@example.invalid"],
        cwd=root,
        check=True,
    )
    historical = root / "historical.txt"
    historical.write_text(
        "/"
        + "home"
        + "/release-auditor/private-run\n"
        + "person"
        + "@historical-domain.com\n"
        + "ghp_"
        + "A" * 36
        + "\n"
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add historical fixture"], cwd=root, check=True)
    historical.unlink()
    subprocess.run(["git", "add", "-u"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "remove historical fixture"], cwd=root, check=True)

    history = module.scan_history(root)

    assert history["passed"] is False
    assert history["review_required_count"] == 3
    assert history["categories"]["private_paths"]["review_required_count"] == 1
    assert history["categories"]["email_addresses"]["review_required_count"] == 1
    assert history["categories"]["credential_or_token_shapes"]["review_required_count"] == 1
    serialized = json.dumps(history)
    assert "release-auditor" not in serialized
    assert "historical-domain.com" not in serialized
    assert "ghp_" not in serialized


def test_history_inventory_fails_closed_outside_git(tmp_path: Path) -> None:
    module = _load_script()

    history = module.scan_history(tmp_path)

    assert history["passed"] is False
    assert history["failure_count"] == 1
    assert history["failures"] == ["git_log_patch_scan_failed"]


def test_committed_inventory_matches_current_public_tree() -> None:
    module = _load_script()
    committed = json.loads(
        (REPOSITORY_ROOT / "artifacts/public-release-inventory.json").read_text()
    )

    untracked = REPOSITORY_ROOT / "inventory-untracked-regression.txt"
    untracked.write_text("This untracked scratch file must not change the locked inventory.\n")
    try:
        assert module.build_inventory(REPOSITORY_ROOT, tracked_only=True) == committed
    finally:
        untracked.unlink()
