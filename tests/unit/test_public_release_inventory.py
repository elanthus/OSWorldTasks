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


def _init_repository(root: Path) -> None:
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q"],
        cwd=root,
        check=True,
    )


def _publish_head(root: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=root,
        check=True,
    )


def test_inventory_detects_public_release_regressions(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE, root)
    (root / ".gitignore").write_text("ignored/\n")
    _init_repository(root)
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture" + "@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "add", "README.md", "evidence.json", "LICENSE", "NOTICE", ".gitignore"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "add public fixture"], cwd=root, check=True)
    _publish_head(root)

    clean = module.build_inventory(root)
    assert clean["summary"]["passed"] is True
    assert clean["links"]["local_link_count"] == 2
    assert all(
        link["target"] != "not-a-real-file.json" for link in clean["links"]["failures"]
    )
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
    _init_repository(root)
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture" + "@example.invalid"],
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
    _publish_head(root)

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
    assert history["failures"] == ["git_public_refs_list_failed"]


def test_history_inventory_fails_closed_without_public_refs(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    root.mkdir()
    _init_repository(root)
    (root / "local-only.txt").write_text("local\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Release Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "local-only history",
        ],
        cwd=root,
        check=True,
    )

    history = module.scan_history(root)

    assert history["passed"] is False
    assert history["failure_count"] == 1
    assert history["failures"] == ["git_public_refs_missing"]


def test_check_mode_ignores_untracked_files_and_reports_tracked_differences(
    tmp_path: Path,
) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE, root)
    _init_repository(root)
    artifact = root / "artifacts" / "public-release-inventory.json"
    artifact.parent.mkdir()
    artifact.write_text("{}\n")
    subprocess.run(
        ["git", "add", "README.md", "evidence.json", "LICENSE", "NOTICE", "artifacts"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture" + "@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "add release fixture"], cwd=root, check=True)
    _publish_head(root)
    artifact.write_text(
        json.dumps(module.build_inventory(root, tracked_only=True), indent=2, sort_keys=True) + "\n"
    )

    untracked = root / "inventory-untracked-regression.txt"
    untracked.write_text("This untracked scratch file must not change the locked inventory.\n")
    check = module.check_committed_inventory(root)
    assert check["passed"] is True
    assert check["history_comparison"]["matched"] is True

    subprocess.run(["git", "add", "-u"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "refresh release fixture"], cwd=root, check=True)
    historical = root / "historical.txt"
    historical.write_text("/" + "home" + "/release-auditor/private-run\n")
    subprocess.run(["git", "add", "historical.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add historical fixture"], cwd=root, check=True)
    historical.unlink()
    subprocess.run(["git", "add", "-u"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "remove historical fixture"], cwd=root, check=True)
    _publish_head(root)

    history_mismatch = module.check_committed_inventory(root)
    assert history_mismatch["passed"] is True
    assert history_mismatch["difference_count"] == 0
    assert history_mismatch["history_comparison"]["matched"] is False
    assert history_mismatch["history_comparison"]["difference_count"] > 0

    readme = root / "README.md"
    readme.write_text(readme.read_text() + "\n[New evidence](new-evidence.json)\n")
    mismatch = module.check_committed_inventory(root)
    assert mismatch["passed"] is False
    assert mismatch["difference_count"] > 0
    assert any("links" in difference for difference in mismatch["differences"])


def test_policy_transport_fixture_is_acknowledged_only_as_exact_test_vector(tmp_path: Path) -> None:
    module = _load_script()
    # Read the real fixture so changing its literal cannot silently retain the acknowledgment.
    source_path = "tests/unit/platform/test_policy_subprocess.py"
    text = (REPOSITORY_ROOT / source_path).read_text()
    shape = dict(module.TOKEN_SHAPES)["bearer_token"]
    values = [match.group(0) for match in shape.finditer(text)]
    assert len(values) == 1
    value = values[0]
    assert module._fingerprint(value) == (
        "sha256:9cc60315c6941fa80e3f712444dfb15039e3699777982d92d40a0d8eae4d0f1c"
    )
    root = tmp_path / "repository"
    (root / "tests").mkdir(parents=True)
    fixture = root / "tests" / "fixture.py"
    unacknowledged = root / "tests" / "different.py"
    outside_tests = root / "release.txt"
    fixture.write_text(value)
    unacknowledged.write_text(value + "-different")
    outside_tests.write_text("Release content: " + value)
    result = module.scan_release_surface(root, [fixture, unacknowledged, outside_tests])
    tokens = result["categories"]["credential_or_token_shapes"]
    assert tokens["finding_count"] == 3
    assert tokens["review_required_count"] == 2
    assert {row["path"]: row["classification"] for row in tokens["findings"]} == {
        "tests/fixture.py": "acknowledged_test_vector",
        "tests/different.py": "review_required_credential_shape",
        "release.txt": "review_required_credential_shape",
    }
    _init_repository(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Release Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "credential classification fixtures"], cwd=root, check=True,
    )
    # Remove from HEAD: history must still classify exact test values and reject other shapes.
    fixture.unlink()
    subprocess.run(["git", "add", "-u"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Release Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "remove test fixture"], cwd=root, check=True,
    )
    _publish_head(root)
    history = module.scan_history(root)["categories"]["credential_or_token_shapes"]
    assert history["finding_count"] == 3
    assert history["review_required_count"] == 2
    assert any(row["classification"] == "acknowledged_test_vector" for row in history["findings"])
    assert value not in json.dumps(result)
    assert value not in json.dumps(history)


def test_history_inventory_excludes_local_only_refs(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    root.mkdir()
    _init_repository(root)
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture" + "@example.invalid"],
        cwd=root,
        check=True,
    )
    (root / "public.txt").write_text("public\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "public history"], cwd=root, check=True)
    _publish_head(root)
    public_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    stash_value = "/" + "home" + "/stash-only/private-run"
    (root / "stash-only.txt").write_text(stash_value + "\n")
    subprocess.run(["git", "add", "stash-only.txt"], cwd=root, check=True)
    subprocess.run(["git", "stash", "push", "-qm", "local-only"], cwd=root, check=True)

    checkpoint_value = "/" + "home" + "/checkpoint-only/private-run"
    subprocess.run(["git", "switch", "-qc", "scratch"], cwd=root, check=True)
    (root / "checkpoint-only.txt").write_text(checkpoint_value + "\n")
    subprocess.run(["git", "add", "checkpoint-only.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "checkpoint-only history"], cwd=root, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/codex/turn-diffs/checkpoints/test", "HEAD"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "switch", "-q", public_branch], cwd=root, check=True)
    subprocess.run(["git", "branch", "-D", "scratch"], cwd=root, check=True)

    history = module.scan_history(root)

    assert history["failure_count"] == 0
    assert history["ref_scope"] == ["refs/remotes/origin/main"]
    assert module._fingerprint(stash_value) not in json.dumps(history)
    assert module._fingerprint(checkpoint_value) not in json.dumps(history)


def test_history_finding_identity_is_path_independent_and_paths_are_complete(
    tmp_path: Path,
) -> None:
    module = _load_script()
    root = tmp_path / "repository"
    (root / "pixelgym").mkdir(parents=True)
    _init_repository(root)
    subprocess.run(["git", "config", "user.name", "Release Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-fixture" + "@example.invalid"],
        cwd=root,
        check=True,
    )
    value = "/" + "home" + "/operator/private-run"
    original = root / "pixelgym" / "fixture.txt"
    original.write_text(value + "\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add fixture"], cwd=root, check=True)
    _publish_head(root)
    initial_findings = module.scan_history(root)["categories"]["private_paths"]["findings"]
    initial = next(
        row for row in initial_findings if row["value_fingerprint"] == module._fingerprint(value)
    )

    (root / "legacy").mkdir()
    subprocess.run(
        ["git", "mv", "pixelgym/fixture.txt", "legacy/fixture.txt"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "move fixture"], cwd=root, check=True)
    _publish_head(root)

    findings = module.scan_history(root)["categories"]["private_paths"]["findings"]
    finding = next(row for row in findings if row["value_fingerprint"] == module._fingerprint(value))

    assert finding["path"] == "legacy/fixture.txt"
    assert finding["paths"] == ["legacy/fixture.txt", "pixelgym/fixture.txt"]
    assert finding["finding_identity"] == initial["finding_identity"]
