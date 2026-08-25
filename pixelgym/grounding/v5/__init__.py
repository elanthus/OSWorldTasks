"""PixelGym v5 stateful end-to-end agent benchmark.

The package is deliberately separate from the frozen v3/v4 workloads.  It
adds task generation, evidence, and runner contracts without changing the
core :class:`pixelgym.env.PixelGuiEnv` observation, action, or reward API.
"""

from pixelgym.grounding.v5.contracts import (
    PROTOCOL_VERSION,
    Partition,
    WorkflowFamily,
)
from pixelgym.grounding.v5.generator import generate_task, tasks_for_partition

__all__ = [
    "PROTOCOL_VERSION",
    "Partition",
    "WorkflowFamily",
    "generate_task",
    "tasks_for_partition",
]
