"""The platform Compose wrapper must make source provenance unavoidable at startup."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

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


def test_down_skips_provenance_so_directory_trap_can_be_recovered(script, tmp_path, monkeypatch) -> None:
    (tmp_path / script.PROVENANCE_RELATIVE_PATH).mkdir(parents=True)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script, "prepare_source_provenance", pytest.fail)
    monkeypatch.setattr(script.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0})())

    assert script.main(["down"]) == 0
