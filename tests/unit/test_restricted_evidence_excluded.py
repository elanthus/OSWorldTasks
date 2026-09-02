"""Enforce the ``must_not_commit`` declarations carried in sealed publication evidence.

The v5 publication pipeline records restricted authoritative artifacts — attempt journals
holding provider responses, screenshots, and private policy checkpoints — in the
``excluded_authoritative_artifacts`` block of each ``*publication-relation.json``. That
declaration is data, not enforcement: without this test nothing stops ``git add`` from
committing the very files the sealed evidence says must never be committed.

Both assertions are meaningful on a fresh clone. Tracking status does not depend on the
restricted file existing locally, and ``git check-ignore`` matches a path pattern rather
than a present file, so this guard protects CI as well as a working checkout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".git").exists(),
    reason="requires a git work tree to resolve tracking and ignore status",
)


def declared_exclusions() -> list[tuple[Path, str]]:
    """Return ``(relation_file, declared_path)`` for every ``must_not_commit`` entry."""

    declarations: list[tuple[Path, str]] = []
    relation_files = sorted(REPOSITORY_ROOT.glob("artifacts/**/*publication-relation.json"))
    for relation_file in relation_files:
        relation = json.loads(relation_file.read_text())
        for excluded in relation.get("excluded_authoritative_artifacts", []):
            if excluded.get("git_status") == "must_not_commit":
                declarations.append((relation_file, excluded["path"]))
    return declarations


def test_publication_relations_declare_restricted_artifacts() -> None:
    # Guards the parse itself: a schema change that silently emptied the declaration list
    # would otherwise make every assertion below vacuously true.
    assert declared_exclusions()


@pytest.mark.parametrize(
    ("relation_file", "declared_path"),
    declared_exclusions(),
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_restricted_artifact_is_untracked(relation_file: Path, declared_path: str) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", declared_path],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert tracked.returncode != 0, (
        f"{declared_path} is tracked, but {relation_file.name} declares it must_not_commit"
    )


@pytest.mark.parametrize(
    ("relation_file", "declared_path"),
    declared_exclusions(),
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_restricted_artifact_is_ignored(relation_file: Path, declared_path: str) -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", declared_path],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert ignored.returncode == 0, (
        f"{declared_path} is not covered by .gitignore, but {relation_file.name} declares "
        "it must_not_commit; a plain `git add` would commit restricted provider evidence"
    )
