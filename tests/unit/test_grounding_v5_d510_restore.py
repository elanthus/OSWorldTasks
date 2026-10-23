"""No-call restore metadata must not conceal any changed scientific result."""

import json

import pytest

from pixelgym.grounding.v5.contracts import sha256_bytes
from scripts.restore_grounding_v5_d510 import drill, final_summary_bytes


def fixture():
    snapshot = {"results": [{"success": False}], "retention": None, "completed": 1}
    receipt = {
        "archive": {"manifest_digest": "fixture-digest"},
        "backup": {"manifest_digest": "fixture-digest"},
    }
    final = {**snapshot, "retention": {**receipt, "manifest_digest": "fixture-digest"}}
    raw = json.dumps(final).encode()
    projection = {
        "source_artifacts": {"summary.json": "sha256:" + sha256_bytes(raw)},
        "retention": receipt,
    }
    return snapshot, raw, projection


def test_restore_allows_only_the_bound_receipt_addition():
    snapshot, final, projection = fixture()
    before = json.dumps(snapshot).encode()
    assert final_summary_bytes(before, final, projection) == final
    assert snapshot["retention"] is None


@pytest.mark.parametrize("change", ["outcome", "count", "receipt", "final", "binding"])
def test_restore_rejects_result_or_receipt_changes(change):
    snapshot, final, projection = fixture()
    if change == "outcome":
        snapshot["results"][0]["success"] = True
    elif change == "count":
        snapshot["completed"] = 2
    elif change == "receipt":
        snapshot["retention"] = projection["retention"]
    elif change == "final":
        final = final.replace(b"false", b"true")
    elif change == "binding":
        projection["source_artifacts"]["summary.json"] = "wrong"
    with pytest.raises(ValueError):
        final_summary_bytes(json.dumps(snapshot).encode(), final, projection)


@pytest.mark.parametrize("nested", [True, False])
def test_restore_rejects_unsafe_destination_before_writing(tmp_path, nested):
    run, archive, backup = (tmp_path / name for name in ("run", "archive", "backup"))
    archive.mkdir()
    if nested:
        destination = archive / "restore"
    else:
        link = tmp_path / "link"
        link.symlink_to(archive, target_is_directory=True)
        destination = link / "restore"
    with pytest.raises(ValueError):
        drill(run, archive, backup, destination)
    assert not (archive / "restore").exists()
