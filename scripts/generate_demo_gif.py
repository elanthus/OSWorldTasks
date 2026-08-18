#!/usr/bin/env python3
"""Generate the review-only real-episode GIF from stored OSWorld evidence."""

from __future__ import annotations

import json
from pathlib import Path

from pixelgym.portfolio import generate_episode_gif


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    output_directory = repository_root / "artifacts" / "day-3" / "review"
    metadata = generate_episode_gif(
        repository_root=repository_root,
        evidence_path=repository_root / "artifacts" / "day-2" / "raw" / "real-golden-episode.json",
        frames_directory=repository_root / "artifacts" / "day-2" / "real-golden" / "frames",
        output_path=output_directory / "real-osworld-episode.gif",
        metadata_path=output_directory / "real-osworld-episode.json",
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
