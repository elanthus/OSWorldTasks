"""Typed task, submission, and evaluation-result contracts (D1.4).

These are the shapes that cross the privileged boundary between the
host-side evaluator and everything else (the environment, the fake and
OSWorld backends, tests). `TaskSpec` and `Submission` are immutable -- a
value handed to a caller cannot be mutated and fed back as if it were still
authoritative, and constructing one copies the caller's mapping rather than
aliasing it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _frozen_mapping(values: Mapping[str, Any]) -> MappingProxyType[str, Any]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class TaskSpec:
    """A single episode's fixed task, derived deterministically from a seed."""

    task_id: str
    seed: int
    instruction: str
    expected_fields: Mapping[str, Any]
    max_episode_steps: int
    app_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_fields", _frozen_mapping(self.expected_fields))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError(f"TaskSpec.seed must be an int, got {type(self.seed).__name__}")
        if not self.expected_fields:
            raise ValueError("TaskSpec.expected_fields must not be empty")
        if isinstance(self.max_episode_steps, bool) or not isinstance(self.max_episode_steps, int):
            raise TypeError(
                "TaskSpec.max_episode_steps must be an int, got "
                f"{type(self.max_episode_steps).__name__}"
            )
        if self.max_episode_steps <= 0:
            raise ValueError(
                f"TaskSpec.max_episode_steps must be positive, got {self.max_episode_steps}"
            )

    @classmethod
    def from_generated(
        cls,
        record: Mapping[str, Any],
        *,
        instruction: str,
        app_url: str,
        max_episode_steps: int,
    ) -> TaskSpec:
        """Build a TaskSpec from a raw generator record (``task_id``, ``seed``,
        ``fields``, ...; see ``pixelgym.tasks.vendor_form.generator``)."""
        return cls(
            task_id=record["task_id"],
            seed=record["seed"],
            instruction=instruction,
            expected_fields=record["fields"],
            max_episode_steps=max_episode_steps,
            app_url=app_url,
        )


@dataclass(frozen=True)
class Submission:
    """One immutable submission event, as recorded by the privileged backend."""

    task_id: str
    seed: int
    values: Mapping[str, Any]
    submitted_at_step: int
    final: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _frozen_mapping(self.values))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError(f"Submission.seed must be an int, got {type(self.seed).__name__}")
        if isinstance(self.submitted_at_step, bool) or not isinstance(self.submitted_at_step, int):
            raise TypeError(
                "Submission.submitted_at_step must be an int, got "
                f"{type(self.submitted_at_step).__name__}"
            )
        if self.submitted_at_step <= 0:
            raise ValueError(
                f"Submission.submitted_at_step must be positive, got {self.submitted_at_step}"
            )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Submission:
        """Build a Submission from a privileged ``/api/state`` submission
        record (see ``SubmissionRecord.to_dict`` in the vendor-form app)."""
        return cls(
            task_id=record["task_id"],
            seed=record["seed"],
            values=record["values"],
            submitted_at_step=record["submitted_at_step"],
            final=record.get("final", True),
        )


@dataclass(frozen=True)
class EvaluationResult:
    """Structured evidence from the privileged evaluator. `success` is the
    only field the environment may convert into reward."""

    success: bool
    score: float
    submitted: bool
    mismatched_fields: tuple[str, ...]
    task_id_matches: bool
