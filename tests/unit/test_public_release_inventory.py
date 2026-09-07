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
    assert clean["links"]["local_link_count"] == 1
    assert clean["license_inventory"]["passed"] is True
    assert clean["redaction_and_asset_inventory"]["review_required_count"] == 0

    readme = root / "README.md"
    ignored = root / "ignored" / "result.json"
    ignored.parent.mkdir()
    ignored.write_text("{}\n")
    readme.write_text(readme.read_text() + "\n[Ignored private evidence](ignored/result.json)\n")
    sensitive = "/" + "Users" + "/alice/private-run\n" + "ghp_" + "A" * 36
    (root / "release-notes.txt").write_text(sensitive)

    regressed = module.build_inventory(root)
    assert regressed["links"]["failure_count"] == 1
    assert regressed["redaction_and_asset_inventory"]["review_required_count"] == 3
    categories = regressed["redaction_and_asset_inventory"]["categories"]
    assert categories["private_paths"]["review_required_count"] == 1
    assert categories["usernames"]["review_required_count"] == 1
    assert categories["credential_or_token_shapes"]["review_required_count"] == 1


def test_committed_inventory_matches_current_public_tree() -> None:
    module = _load_script()
    committed = json.loads((REPOSITORY_ROOT / "artifacts/public-release-inventory.json").read_text())

    assert module.build_inventory(REPOSITORY_ROOT) == committed
