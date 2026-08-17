"""Proves the package installs and imports cleanly without the optional OSWorld extra."""

import importlib
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
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    dev_requirements = project["optional-dependencies"]["dev"]

    assert any(requirement.startswith("httpx>=") for requirement in dev_requirements)
