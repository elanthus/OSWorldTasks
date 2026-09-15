"""Record deferred choices without a correctness oracle before final commit."""

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pixelgym.grounding.v5.backend import V5FakeBackend, VisibleControl
from pixelgym.grounding.v5.memory_generator import generate_memory_task
from pixelgym.serialization import canonical_json_bytes


class MemoryBackend(V5FakeBackend):
    backend_identity = "pixelgym-v5-memory-fake-backend-v2"

    def __init__(self) -> None:
        super().__init__(task_factory=generate_memory_task)
        self._deferred_choices: dict[int, str] = {}

    def reset(self, seed: int) -> Mapping[str, Any]:
        record = super().reset(seed)
        self._deferred_choices = {}
        return record

    def visible_controls(self) -> tuple[VisibleControl, ...]:
        return tuple(
            replace(control, label="Confirm the recorded selection")
            if control.control_id == "repair_implicated"
            else control
            for control in super().visible_controls()
        )

    def click(self, x: int, y: int) -> None:
        task = self._require_active()
        if self._irreversible_failure or self._stage_index >= len(task.stages):
            return super().click(x, y)
        stage = task.stages[self._stage_index]
        clicked = next(
            (
                control.control_id
                for control in self.visible_controls()
                if control.bbox[0] <= x < control.bbox[2] and control.bbox[1] <= y < control.bbox[3]
            ),
            None,
        )
        if self._stage_index in (5, 7) and not self._repair_pending and clicked is not None:
            self._action_count += 1
            self._deferred_choices[self._stage_index] = clicked
            if stage.recovery_stage:
                self._intentional_errors_entered.add(self._stage_index)
                self._repair_pending = True
                # This same deterministic recovery occurs for every selection.
                self._error(
                    "Verification requires confirmation of the recorded selection.",
                    "entered_declared_recovery",
                )
            else:
                self._advance("memory_choice_recorded")
            return
        if (
            self._stage_index == len(task.stages) - 1
            and clicked == stage.target_control_id
            and any(
                self._deferred_choices.get(index) != task.stages[index].target_control_id
                for index in (5, 7)
            )
        ):
            self._action_count += 1
            self._seal_wrong_commit("unmatched_deferred_reference")
            return
        super().click(x, y)

    def checkpoint(self) -> bytes:
        return canonical_json_bytes(
            {
                **json.loads(super().checkpoint()),
                "schema_version": "pixelgym-v5-memory-checkpoint-v2",
                "deferred_choices": {
                    str(key): value for key, value in self._deferred_choices.items()
                },
            }
        )

    def restore(self, checkpoint: bytes) -> None:
        value = json.loads(checkpoint)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "pixelgym-v5-memory-checkpoint-v2"
        ):
            raise ValueError("unsupported memory checkpoint schema")
        if not {"seed", "stage_index", "repair_pending"} <= value.keys():
            raise ValueError("memory checkpoint is missing required fields")
        choices = value.get("deferred_choices")
        if not isinstance(choices, dict) or not set(choices) <= {"5", "7"}:
            raise ValueError("invalid deferred-choice checkpoint")
        task = self.task_factory(value["seed"])
        for index in (5, 7):
            selected = choices.get(str(index))
            visited = value["stage_index"] > index or (index == 7 and value["repair_pending"])
            if (selected is not None) != visited:
                raise ValueError("deferred choices do not match the checkpoint stage")
            if selected is not None and selected not in {
                c.control_id for c in task.stages[index].controls
            }:
                raise ValueError("deferred choice is not a declared control")
        legacy = {key: item for key, item in value.items() if key != "deferred_choices"}
        legacy["schema_version"] = "pixelgym-v5-fake-checkpoint-v1"
        super().restore(canonical_json_bytes(legacy))
        self._deferred_choices = {int(key): item for key, item in choices.items()}
