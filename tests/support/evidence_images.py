"""Skip guards for tests that open release-hosted evidence PNGs.

The PNG sets listed in ``scripts.package_evidence_images.HOSTED_SETS`` are
published as assets of the ``evidence-images-v1`` GitHub release rather than
kept in the tree. A test that opens one of those PNGs uses ``requires_images``
so a fresh clone without the images reports a skip naming the fetch command,
and a clone with the images fetched runs the test in full.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.package_evidence_images import images_present

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FETCH_COMMAND = "python scripts/fetch_evidence_images.py"


def manifest_images_present(set_name: str) -> bool:
    """True when every PNG in ``artifacts/<set>/images.manifest.json`` is on disk."""
    return images_present(REPOSITORY_ROOT / "artifacts", set_name)


def requires_images(set_name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not manifest_images_present(set_name),
        reason=(
            f"artifacts/{set_name} PNGs are release-hosted and not fetched; "
            f"run `{FETCH_COMMAND} --set {set_name}`"
        ),
    )
