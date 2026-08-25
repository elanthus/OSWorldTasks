"""V5 backend checkpoint/reconnect extension without changing the core protocol."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from pixelgym.grounding.v5.contracts import EnvironmentResumeRecord


@runtime_checkable
class ResumableV5Backend(Protocol):
    def checkpoint(self) -> bytes: ...
    def restore(self, checkpoint: bytes) -> None: ...
    def environment_resume_record(self, *, step_count: int) -> EnvironmentResumeRecord: ...
    def verify_resume_record(
        self, record: EnvironmentResumeRecord, *, step_count: int
    ) -> None: ...


def decode_resume_record(data: bytes) -> EnvironmentResumeRecord:
    value: dict[str, Any] = json.loads(data)
    return EnvironmentResumeRecord(**value)
