"""Proves the package installs and imports cleanly without the optional OSWorld extra."""

import importlib


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
