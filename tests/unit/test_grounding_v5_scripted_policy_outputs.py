"""Scripted-policy freeze outputs are created exclusively and never clobbered."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.freeze_grounding_v5_scripted_policy import (
    fresh_output_paths,
    write_fresh_outputs,
)


def test_scripted_policy_outputs_reject_resolved_path_aliases(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "policy.json"
    alias = output.parent / ".." / "outputs" / output.name
    with pytest.raises(RuntimeError, match="distinct fresh paths"):
        fresh_output_paths(output, alias)


def test_scripted_policy_output_creation_is_exclusive_and_preserves_partial_pair(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    plan = tmp_path / "plan.json"
    manifest_path, plan_path = fresh_output_paths(manifest, plan)
    plan_path.write_bytes(b"concurrent-writer")

    with pytest.raises(RuntimeError, match="output set is incomplete") as error:
        write_fresh_outputs(((manifest_path, b"manifest"), (plan_path, b"plan")))

    assert str(manifest_path) in str(error.value)
    assert manifest_path.read_bytes() == b"manifest"
    assert plan_path.read_bytes() == b"concurrent-writer"


def test_scripted_policy_output_failure_never_deletes_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "manifest.json"
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"concurrent-writer")
    original_open = Path.open

    def replacing_open(path: Path, *args: object, **kwargs: object):
        if path == plan:
            manifest.unlink()
            with original_open(manifest, "wb") as handle:
                handle.write(b"replacement")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replacing_open)

    with pytest.raises(RuntimeError, match="output set is incomplete"):
        write_fresh_outputs(((manifest, b"manifest"), (plan, b"plan")))

    assert manifest.read_bytes() == b"replacement"
    assert plan.read_bytes() == b"concurrent-writer"
