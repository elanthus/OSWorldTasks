from __future__ import annotations

from pathlib import Path

import pytest

from pixelgym.backends.base import (
    EnvironmentResumeRecord,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5 import contracts as v5_contracts
from pixelgym.grounding.v5.resume import decode_resume_record
from pixelgym.platform.fingerprints import canonical_json_bytes as platform_canonical_json_bytes
from pixelgym.serialization import (
    canonical_json_bytes,
    canonical_json_text,
    load_jsonl,
    resolve_repository_output,
)
from pixelgym.tasks.vendor_form.generator import canonical_json as task_canonical_json
from pixelgym.validation.metrics import canonical_json as validation_canonical_json


def test_jsonl_loader_skips_blank_lines_and_requires_objects(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('\n{"name":"café"}\n   \n{"value":2}\n', encoding="utf-8")
    assert load_jsonl(path) == [{"name": "café"}, {"value": 2}]

    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="must contain a JSON object"):
        load_jsonl(path)


def test_all_canonical_json_entry_points_share_utf8_identity() -> None:
    value = {"label": "café", "number": 1}
    expected = b'{"label":"caf\xc3\xa9","number":1}'

    assert canonical_json_bytes(value) == expected
    assert canonical_json_text(value).encode("utf-8") == expected
    assert platform_canonical_json_bytes(value) == expected
    assert task_canonical_json(value).encode("utf-8") == expected
    assert validation_canonical_json(value).encode("utf-8") == expected


def test_resume_record_preserves_pre_move_canonical_bytes_and_digest() -> None:
    record = EnvironmentResumeRecord(
        task_id="task-fixed-107",
        backend_identity="pixelgym-core-fake-1024x768-v1",
        step_count=7,
        screenshot_digest="sha256:" + "11" * 32,
        application_state_digest="sha256:" + "22" * 32,
        mechanism="checkpoint_restore",
        checkpoint_digest="sha256:" + "33" * 32,
    )
    expected = (
        b'{"application_state_digest":"sha256:'
        + b"22" * 32
        + b'","backend_identity":"pixelgym-core-fake-1024x768-v1",'
        + b'"checkpoint_digest":"sha256:'
        + b"33" * 32
        + b'","mechanism":"checkpoint_restore","screenshot_digest":"sha256:'
        + b"11" * 32
        + b'","step_count":7,"task_id":"task-fixed-107"}'
    )

    serialized = canonical_json_bytes(record.to_dict())

    assert serialized == expected
    assert decode_resume_record(expected) == record
    assert content_digest(record.to_dict()) == (
        "sha256:c1aa71ecfd475b5d92cf3b64afd4cc49ece028b76e241bf7c343277d8bdc8303"
    )
    assert v5_contracts.EnvironmentResumeRecord is EnvironmentResumeRecord
    assert v5_contracts.content_digest is content_digest
    assert v5_contracts.sha256_bytes is sha256_bytes


def test_repository_output_rejects_traversal_absolute_and_symlink_escape(
    tmp_path: Path,
) -> None:
    assert resolve_repository_output(tmp_path, "artifacts/report.png") == (
        tmp_path / "artifacts/report.png"
    )
    with pytest.raises(ValueError, match="repository-relative"):
        resolve_repository_output(tmp_path, "../outside.png")
    with pytest.raises(ValueError, match="repository-relative"):
        resolve_repository_output(tmp_path, "/tmp/outside.png")

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_repository_output(tmp_path, "linked/out.png")
