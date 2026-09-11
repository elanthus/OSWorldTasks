"""The platform Compose wrapper must make source provenance unavoidable at startup."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

from pixelgym.platform.source_provenance import load_packaged_source_provenance
from tests.integration.platform.conftest import ComposeStack

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

    assert documented_wrapper_commands(root_readme) == []
    assert "deploy/README.md#test-suite-boundaries" in root_readme
    assert documented_wrapper_commands(deploy_readme) == [expected[0], expected[1], *expected[::-1]]
    assert "docker compose --env-file deploy/.env.example -f deploy/compose.yaml up" not in root_readme
    assert "Do not invoke `docker compose`" in deploy_readme


def test_unauthenticated_demo_uis_are_loopback_only_and_documented() -> None:
    compose = (REPOSITORY_ROOT / "deploy/compose.yaml").read_text()
    deploy_readme = (REPOSITORY_ROOT / "deploy/README.md").read_text()

    def service_ports(service: str) -> list[str]:
        lines = compose.splitlines()
        start = lines.index(f"  {service}:")
        for line in lines[start + 1 :]:
            if line.startswith("  ") and not line.startswith("    "):
                break
            if line.startswith("    ports: "):
                return json.loads(line.removeprefix("    ports: "))
        raise AssertionError(f"{service} has no published ports")

    assert service_ports("mlflow") == [
        "127.0.0.1:${PIXELGYM_MLFLOW_PORT:-5500}:5000"
    ]
    assert service_ports("platform") == [
        "127.0.0.1:${PIXELGYM_PLATFORM_PORT:-5800}:8000"
    ]
    warning = next(
        paragraph for paragraph in deploy_readme.split("\n\n") if "shared deployment" in paragraph
    )
    warning = " ".join(warning.split())
    assert "no caller authentication" in warning
    assert "shared network" in warning
    assert "control plane" in warning and "MLflow" in warning


def test_manual_platform_workflow_excludes_compose_lifecycle_suite() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/platform-integration.yml").read_text()
    root_readme = (REPOSITORY_ROOT / "README.md").read_text()
    deploy_readme = (REPOSITORY_ROOT / "deploy/README.md").read_text()

    assert "tests/integration/platform/test_metaflow_runtime.py" in workflow
    assert "tests/integration/platform/test_mlflow_tracking.py" in workflow
    assert "test_compose_lifecycle.py" not in workflow
    assert "does **not** run the Docker/Playwright lifecycle suite" in " ".join(
        root_readme.split()
    )
    assert "does not run the fresh-stack Docker/Playwright lifecycle" in " ".join(
        deploy_readme.split()
    )


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


def test_compose_timeout_reports_redacted_scoped_diagnostics(monkeypatch) -> None:
    stack = ComposeStack(
        repository_root=REPOSITORY_ROOT,
        project="pixelgym-it-timeout",
        environment={},
        platform_port=10001,
        mlflow_port=10002,
        postgres_port=10003,
        minio_port=10004,
    )

    def invoke(self, *arguments: str, timeout: float):
        output = f"{REPOSITORY_ROOT} local_demo_postgres_only command={' '.join(arguments)}"
        if arguments == ("up",):
            raise subprocess.TimeoutExpired(arguments, timeout, output=output)
        return subprocess.CompletedProcess(arguments, 0, stdout=output)

    monkeypatch.setattr(ComposeStack, "_invoke", invoke)
    with pytest.raises(pytest.fail.Exception) as exc_info:
        stack.compose("up", timeout=0.25)

    message = str(exc_info.value)
    assert "Compose command timed out after 0.25s: up" in message
    assert "command=ps --all" in message
    assert "command=logs --no-color --tail 200" in message
    assert str(REPOSITORY_ROOT) not in message
    assert "local_demo_postgres_only" not in message
    assert "<repository>" in message
    assert "<redacted-local-demo-secret>" in message


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
