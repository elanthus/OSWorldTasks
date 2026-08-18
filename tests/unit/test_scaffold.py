"""Proves the package installs and imports cleanly without the optional OSWorld extra."""

import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def test_package_version():
    import pixelgym

    assert pixelgym.__version__ == "0.1.0"


def test_core_modules_import_without_osworld():
    for module in [
        "pixelgym.env",
        "pixelgym.actions",
        "pixelgym.task_spec",
        "pixelgym.evaluator",
        "pixelgym.backends.base",
        "pixelgym.backends.fake",
        "pixelgym.tasks.vendor_form",
    ]:
        importlib.import_module(module)


def test_developer_extra_declares_direct_web_test_client_dependency():
    repository_root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((repository_root / "pyproject.toml").read_text())["project"]
    dev_requirements = project["optional-dependencies"]["dev"]

    assert any(requirement.startswith("httpx>=") for requirement in dev_requirements)


def test_subprocess_imports_package_from_active_checkout(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import pixelgym; print(Path(pixelgym.__file__).resolve())",
        ],
        cwd=outside_checkout,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    imported_path = Path(completed.stdout.strip())
    assert imported_path.is_relative_to(repository_root / "pixelgym")
