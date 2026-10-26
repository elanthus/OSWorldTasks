"""Protocol selection for the paired grounding evaluation runner.

V1 is the frozen, reported benchmark.  V2 is the crossed target-by-screen-state
allocation whose dataset is frozen but has never been evaluated.  Selecting a protocol
changes only which checked-in metadata the runner reads, which protocol identifier it
stamps, and where it writes; the prompt, parser, and scoring rule are shared.  Nothing
in this module performs a model call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pixelgym.grounding.schema import PROTOCOL_VERSION as PROTOCOL_VERSION_V1
from pixelgym.grounding.schema_v2 import PROTOCOL_VERSION as PROTOCOL_VERSION_V2

PROTOCOL_NAMES = ("v1", "v2")


@dataclass(frozen=True)
class GroundingProtocol:
    """Checked-in inputs and output locations for one grounding protocol."""

    name: str
    protocol_version: str
    dataset_path: str
    overlays_path: str
    candidates_path: str
    manifest_path: str | None
    pilot_predictions_path: str
    full_predictions_path: str
    cache_directory: str


V1_PROTOCOL = GroundingProtocol(
    name="v1",
    protocol_version=PROTOCOL_VERSION_V1,
    dataset_path="artifacts/grounding-dataset.jsonl",
    overlays_path="artifacts/grounding-overlays.jsonl",
    candidates_path="artifacts/grounding-candidates.jsonl",
    manifest_path=None,
    pilot_predictions_path="artifacts/day-3/pilot/pilot-predictions.jsonl",
    full_predictions_path="artifacts/grounding-predictions.jsonl",
    cache_directory=".cache/grounding/responses",
)

V2_PROTOCOL = GroundingProtocol(
    name="v2",
    protocol_version=PROTOCOL_VERSION_V2,
    dataset_path="artifacts/grounding-v2-dataset.jsonl",
    overlays_path="artifacts/grounding-v2-overlays.jsonl",
    candidates_path="artifacts/grounding-v2-candidates.jsonl",
    manifest_path="artifacts/grounding-v2-manifest.json",
    pilot_predictions_path="artifacts/grounding-v2/pilot/pilot-predictions.jsonl",
    full_predictions_path="artifacts/grounding-v2-predictions.jsonl",
    cache_directory=".cache/grounding-v2/responses",
)

PROTOCOLS: dict[str, GroundingProtocol] = {"v1": V1_PROTOCOL, "v2": V2_PROTOCOL}


def protocol_for(name: str) -> GroundingProtocol:
    """Return the protocol for a CLI selector value, rejecting unknown names."""
    try:
        return PROTOCOLS[name]
    except KeyError as exc:
        raise ValueError(f"unknown grounding protocol {name!r}") from exc


def v1_prediction_paths(repository_root: Path) -> tuple[Path, ...]:
    """Frozen v1 result files that no other protocol may ever write."""
    return (
        repository_root / V1_PROTOCOL.pilot_predictions_path,
        repository_root / V1_PROTOCOL.full_predictions_path,
    )
