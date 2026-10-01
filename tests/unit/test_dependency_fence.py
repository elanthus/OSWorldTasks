"""Dependency fence: the core install must not need the platform extra.

`anyio` and `jsonschema` are declared only by the ``platform`` extra. The core environment,
fake backend, grounding v1 modules, and task app must import without either package, so these
tests inspect ``sys.modules`` in a fresh interpreter and the packaging metadata. They also guard
the hardened container recipes offline (no Docker build, no network).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

GROUNDING_V1_MODULES = tuple(
    f"pixelgym.grounding.{name}"
    for name in (
        "analysis",
        "benchmark_v2",
        "capture",
        "determinism",
        "evaluation",
        "overlays",
        "pilot",
        "providers",
        "report",
        "report_v2",
        "schema",
        "stats",
        "verification",
    )
)
# Modules that do not serve HTTP: neither fenced package may be loaded at all.
CORE_MODULES = (
    "pixelgym",
    "pixelgym.env",
    "pixelgym.backends.fake",
    "pixelgym.tasks.vendor_form.app.guest_server",
)
# FastAPI is a core dependency and Starlette imports anyio itself, so for these modules only
# jsonschema can be absent from sys.modules; the anyio fence is enforced at source level below.
FASTAPI_MODULES = ("pixelgym.tasks.vendor_form.app.server", *GROUNDING_V1_MODULES)
FENCED = ("anyio", "jsonschema")
_FENCED_IMPORT = re.compile(r"^\s*(?:import|from)\s+(anyio|jsonschema)\b", re.MULTILINE)


def _loaded_top_level_packages(modules: tuple[str, ...]) -> set[str]:
    script = (
        "import importlib, json, sys\n"
        f"for name in {list(modules)!r}:\n"
        "    importlib.import_module(name)\n"
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return set(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_core_modules_load_neither_fenced_package() -> None:
    loaded = _loaded_top_level_packages(CORE_MODULES)
    assert loaded.isdisjoint(FENCED), sorted(loaded & set(FENCED))


def test_task_app_and_grounding_v1_do_not_load_jsonschema() -> None:
    loaded = _loaded_top_level_packages(FASTAPI_MODULES)
    assert "jsonschema" not in loaded


@pytest.mark.parametrize("module", [*CORE_MODULES, *FASTAPI_MODULES])
def test_core_module_sources_do_not_import_fenced_packages(module: str) -> None:
    relative = Path(*module.split("."))
    source = REPOSITORY_ROOT / (
        relative / "__init__.py"
        if (REPOSITORY_ROOT / relative).is_dir()
        else relative.with_suffix(".py")
    )
    assert _FENCED_IMPORT.search(source.read_text(encoding="utf-8")) is None


def test_non_platform_package_modules_import_fenced_packages_only_lazily() -> None:
    offenders = []
    for path in sorted((REPOSITORY_ROOT / "pixelgym").rglob("*.py")):
        relative = path.relative_to(REPOSITORY_ROOT)
        if relative.parts[:2] == ("pixelgym", "platform"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Module-level (unindented) imports are eager; function-local ones are lazy.
            if _FENCED_IMPORT.match(line) and not line.startswith((" ", "\t")):
                offenders.append(f"{relative}:{number}")
    assert offenders == []


def test_fenced_packages_are_declared_only_by_the_platform_extra() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    def names(requirements: list[str]) -> set[str]:
        return {re.split(r"[\s\[<>=~!;]", item, maxsplit=1)[0].lower() for item in requirements}

    assert names(project["dependencies"]).isdisjoint(FENCED)
    assert set(FENCED) <= names(project["optional-dependencies"]["platform"])


# --- Container hardening (parsed offline; images are not built here) ---------------------------


def _instructions(dockerfile: Path) -> list[tuple[str, str]]:
    joined = re.sub(r"\\\n", " ", dockerfile.read_text(encoding="utf-8"))
    result = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, _, rest = stripped.partition(" ")
        result.append((keyword.upper(), rest.strip()))
    return result


@pytest.mark.parametrize("name", ["Dockerfile.platform", "Dockerfile.mlflow"])
def test_dockerfile_installs_only_hash_locked_requirements_and_drops_root(name: str) -> None:
    instructions = _instructions(REPOSITORY_ROOT / "deploy" / name)
    pip_installs = [
        index
        for index, (keyword, rest) in enumerate(instructions)
        if keyword == "RUN" and "pip install" in rest
    ]
    assert pip_installs, "expected at least one pip install"

    lock_references = []
    for index in pip_installs:
        command = instructions[index][1]
        requirement_files = re.findall(r"(?:-r|--requirement)\s+(\S+)", command)
        if requirement_files:
            assert "--require-hashes" in command.split(), command
            lock_references.extend(requirement_files)
        else:
            # Only the repository itself may be installed without a lock, and never its deps.
            assert "--no-deps" in command.split(), command
    assert lock_references, "every image must install a hash-locked requirements file"

    copies = {
        rest.split()[-1]: rest.split()[-2]
        for keyword, rest in instructions
        if keyword == "COPY" and not rest.startswith("--from")
    }
    for reference in lock_references:
        source = next(
            (src for dst, src in copies.items() if dst == reference or dst.endswith(reference)),
            None,
        )
        assert source is not None, f"{reference} is not copied from the repository"
        lock = REPOSITORY_ROOT / source
        assert lock.is_file(), source
        text = lock.read_text(encoding="utf-8")
        assert "--hash=sha256:" in text
        assert re.search(r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==\S+", text, re.MULTILINE)

    users = [index for index, (keyword, _) in enumerate(instructions) if keyword == "USER"]
    assert users and users[-1] > pip_installs[-1]
    final_user = instructions[users[-1]][1].split(":")[0]
    assert final_user not in {"root", "0"}


def test_mlflow_lock_pins_the_same_server_stack_as_the_platform_extra() -> None:
    lock = (REPOSITORY_ROOT / "requirements/mlflow-py312.lock").read_text(encoding="utf-8")
    for pin in ("mlflow==3.14.0", "boto3==1.40.1", "psycopg2-binary==2.9.10"):
        assert re.search(rf"^{re.escape(pin)} \\$", lock, re.MULTILINE), pin
