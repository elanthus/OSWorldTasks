"""The platform Compose wrapper must make source provenance unavoidable at startup."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from pixelgym.platform.source_provenance import load_packaged_source_provenance

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/platform_compose.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("platform_compose_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_platform_startup_docs_use_the_provenance_wrapper() -> None:
    root_readme = (REPOSITORY_ROOT / "README.md").read_text()
    deploy_readme = (REPOSITORY_ROOT / "deploy/README.md").read_text()
    expected = [
        "python3.12 scripts/platform_compose.py up --build --wait",
        "python3.12 scripts/platform_compose.py down",
    ]

    def documented_wrapper_commands(text: str) -> list[str]:
        return re.findall(r"^python3\.12 scripts/platform_compose\.py .+$", text, re.MULTILINE)

    assert documented_wrapper_commands(root_readme) == expected
    assert documented_wrapper_commands(deploy_readme) == [expected[0], expected[1], *expected[::-1]]
    assert "docker compose --env-file deploy/.env.example -f deploy/compose.yaml up" not in root_readme
    assert "Do not invoke `docker compose`" in deploy_readme


def test_prepare_source_provenance_creates_a_missing_file(script, tmp_path, monkeypatch) -> None:
    written: list[Path] = []
    monkeypatch.setattr(script, "write_source_provenance", lambda root, path: written.append(path))

    result = script.prepare_source_provenance(tmp_path)

    assert result == tmp_path / script.PROVENANCE_RELATIVE_PATH
    assert written == [result]


def test_prepare_source_provenance_replaces_an_existing_file(script, tmp_path, monkeypatch) -> None:
    path = tmp_path / script.PROVENANCE_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("stale manifest\n")
    written: list[Path] = []
    monkeypatch.setattr(script, "write_source_provenance", lambda root, output: written.append(output))

    result = script.prepare_source_provenance(tmp_path)

    assert result == path
    assert written == [path]


def test_prepare_source_provenance_refuses_a_directory(script, tmp_path, monkeypatch) -> None:
    path = tmp_path / script.PROVENANCE_RELATIVE_PATH
    path.mkdir(parents=True)
    monkeypatch.setattr(script, "write_source_provenance", pytest.fail)

    with pytest.raises(RuntimeError, match="directory, not the required provenance file") as exc_info:
        script.prepare_source_provenance(tmp_path)

    assert f"rmdir {path}" in str(exc_info.value)


def test_documented_wrapper_generates_a_verifiable_manifest(script, tmp_path, monkeypatch) -> None:
    """The only documented startup entrypoint writes the manifest it bind-mounts."""
    monkeypatch.setattr(script, "PROVENANCE_RELATIVE_PATH", tmp_path / "source-provenance.json")

    manifest = script.prepare_source_provenance(REPOSITORY_ROOT)

    provenance = load_packaged_source_provenance(REPOSITORY_ROOT, manifest)
    assert provenance.state in {"clean", "dirty"}
    assert provenance.failure_reason is None


def test_no_legacy_provenance_generator_can_drift_from_the_compose_wrapper() -> None:
    legacy_generator = REPOSITORY_ROOT / "scripts/prepare_platform_source_provenance.py"

    assert not legacy_generator.exists()
    for documentation in (REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "deploy/README.md"):
        assert "prepare_platform_source_provenance.py" not in documentation.read_text()


@pytest.mark.parametrize("command", ["up", "start", "restart", "run"])
def test_container_start_commands_prepare_source_provenance(script, tmp_path, monkeypatch, command: str) -> None:
    prepared: list[Path] = []
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script, "prepare_source_provenance", lambda root: prepared.append(root))
    monkeypatch.setattr(script.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0})())

    assert script.main([command]) == 0
    assert prepared == [tmp_path]


@pytest.mark.parametrize("command", ["up", "start", "restart", "run"])
def test_container_start_commands_refuse_the_directory_trap(script, tmp_path, monkeypatch, capsys, command: str) -> None:
    (tmp_path / script.PROVENANCE_RELATIVE_PATH).mkdir(parents=True)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script.subprocess, "run", pytest.fail)

    assert script.main([command]) == 2
    assert "directory, not the required provenance file" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["down", "stop", "ps", "logs"])
def test_non_start_commands_skip_provenance_so_directory_trap_can_be_recovered(
    script, tmp_path, monkeypatch, command: str
) -> None:
    (tmp_path / script.PROVENANCE_RELATIVE_PATH).mkdir(parents=True)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script, "prepare_source_provenance", pytest.fail)
    monkeypatch.setattr(script.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0})())

    assert script.main([command]) == 0
