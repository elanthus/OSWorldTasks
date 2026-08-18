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


def test_developer_extra_declares_parallel_test_runner() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((repository_root / "pyproject.toml").read_text())["project"]
    dev_requirements = project["optional-dependencies"]["dev"]

    assert any(requirement.startswith("pytest-xdist>=") for requirement in dev_requirements)


def test_subprocess_imports_package_from_active_checkout(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()

    decoy_checkout = tmp_path / "decoy-checkout"
    decoy_package = decoy_checkout / "pixelgym"
    decoy_package.mkdir(parents=True)
    (decoy_package / "__init__.py").write_text("DECOY = True\n")

    environment = os.environ.copy()
    inherited_pythonpath = environment.get("PYTHONPATH", "").split(os.pathsep)
    environment["PYTHONPATH"] = os.pathsep.join(
        [inherited_pythonpath[0], str(decoy_checkout), *inherited_pythonpath[1:]]
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; import pixelgym; "
                "print(Path(os.environ['PYTHONPATH'].split(os.pathsep)[0]).resolve()); "
                "print(Path(pixelgym.__file__).resolve())"
            ),
        ],
        cwd=outside_checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    first_pythonpath, imported_path_text = completed.stdout.splitlines()
    assert Path(first_pythonpath) == repository_root
    imported_path = Path(imported_path_text)
    assert imported_path.is_relative_to(repository_root / "pixelgym")
