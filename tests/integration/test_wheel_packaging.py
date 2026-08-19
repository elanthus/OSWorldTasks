"""Installed-wheel smoke test for packaged application assets.

Builds the pixelgym wheel without build isolation and installs it into an
isolated directory with ``pip install --no-deps``. No virtual environment,
dependency resolution, or network access is needed. A subprocess then imports
the package and serves ``/`` from a working directory outside the source
checkout, proving the static HTML/CSS/JS and the bundled font are genuinely
packaged rather than merely reachable via the repo's cwd.

Heavier than the unit tests (it shells out to build a wheel), so it lives in
tests/integration/ rather than tests/unit/, per the project's test layout.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
from email.parser import Parser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_STATIC_ASSETS = (
    "index.html",
    "app.js",
    "style.css",
    "fonts/DejaVuSans.ttf",
    "fonts/DejaVuSans-Bold.ttf",
)


@pytest.fixture(scope="module")
def installed_wheel_site_dir():
    with (
        tempfile.TemporaryDirectory(prefix="pixelgym-wheel-build-") as build_dir,
        tempfile.TemporaryDirectory(prefix="pixelgym-wheel-install-") as install_dir,
    ):
        build_dir = Path(build_dir)
        install_dir = Path(install_dir)

        build = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(REPO_ROOT),
                "--no-deps",
                "--no-build-isolation",
                "-w",
                str(build_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr

        wheels = list(build_dir.glob("pixelgym-*.whl"))
        assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"

        install = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(install_dir),
                str(wheels[0]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert install.returncode == 0, install.stdout + install.stderr

        yield install_dir


def test_static_assets_are_packaged_in_the_wheel(installed_wheel_site_dir):
    app_static = installed_wheel_site_dir / "pixelgym" / "tasks" / "vendor_form" / "app" / "static"

    for relative_path in _STATIC_ASSETS:
        assert (app_static / relative_path).is_file(), relative_path


def test_platform_schemas_are_packaged_in_the_wheel(installed_wheel_site_dir):
    schemas = installed_wheel_site_dir / "pixelgym" / "platform" / "schemas"
    expected = {path.name for path in (REPO_ROOT / "config").glob("*.schema.json")}
    installed = {path.name for path in schemas.glob("*.schema.json")}

    assert installed == expected


def test_apache_license_metadata_and_text_are_packaged(installed_wheel_site_dir):
    dist_info_dirs = list(installed_wheel_site_dir.glob("pixelgym-*.dist-info"))
    assert len(dist_info_dirs) == 1, dist_info_dirs
    dist_info = dist_info_dirs[0]
    metadata = Parser().parsestr((dist_info / "METADATA").read_text())

    assert metadata["License-Expression"] == "Apache-2.0"
    license_text = (dist_info / "licenses" / "LICENSE").read_text()
    assert license_text.lstrip().startswith("Apache License\n")
    assert "Version 2.0, January 2004" in license_text
    assert (dist_info / "licenses" / "NOTICE").read_text() == (
        "PixelGym-OSWorld\nCopyright 2026 Michael Swailes\n"
    )


def test_installed_wheel_serves_index_html_from_outside_the_source_checkout(
    installed_wheel_site_dir, tmp_path
):
    outside_cwd = tmp_path / "definitely-not-the-source-checkout"
    outside_cwd.mkdir()
    assert not str(outside_cwd).startswith(str(REPO_ROOT))

    script = textwrap.dedent(
        """
        from fastapi.testclient import TestClient
        from pixelgym.tasks.vendor_form.app.server import create_app

        client = TestClient(create_app())
        client.post("/api/reset", json={"seed": 1})
        response = client.get("/")
        assert response.status_code == 200, response.status_code
        assert "text/html" in response.headers["content-type"]
        assert "PixelGym Sans" in response.text or "/static/style.css" in response.text
        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=outside_cwd,
        env={**os.environ, "PYTHONPATH": str(installed_wheel_site_dir)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_installed_wheel_constructs_control_store_outside_source_checkout(
    installed_wheel_site_dir, tmp_path
):
    outside_cwd = tmp_path / "control-store-outside-source"
    outside_cwd.mkdir()
    script = textwrap.dedent(
        """
        from pixelgym.platform.control_store import ControlStore

        control = ControlStore(":memory:", reviewer_identity="local-reviewer")
        control.migrate()
        assert control.list_submissions() == []
        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=outside_cwd,
        env={**os.environ, "PYTHONPATH": str(installed_wheel_site_dir)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
