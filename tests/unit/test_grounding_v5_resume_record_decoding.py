"""Resume records must decode to a JSON object."""

from __future__ import annotations

import pytest

from pixelgym.grounding.v5.resume import decode_resume_record


@pytest.mark.parametrize("payload", [b"[]", b"null", b'"record"', b"1"])
def test_resume_record_decoder_rejects_non_object_json(payload: bytes) -> None:
    with pytest.raises(ValueError, match="must be an object"):
        decode_resume_record(payload)
