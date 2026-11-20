#!/usr/bin/env python3
"""Generate v2 of the target-clustered grounding supplement: v1 with its allocation stated in full.

v1 (``artifacts/grounding-clustered-analysis-v1.{json,md}``) describes the 100 examples as
"10 target controls x 10 seeds", which reads as 10 seeds in all. In the frozen dataset each
target appears on 10 distinct task seeds, always in one screen state, and the set spans 20 task
seeds x 5 screen states. v1 is left as it was. v2 recomputes every number with the v1 code (they
are unchanged), reads the allocation from ``artifacts/grounding-dataset.jsonl``, and states it.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_grounding_clustered_analysis as v1

SCHEMA_VERSION = "pixelgym-grounding-clustered-analysis-v2"
DATASET_PATH = "artifacts/grounding-dataset.jsonl"
JSON_OUTPUT = "artifacts/grounding-clustered-analysis-v2.json"
MD_OUTPUT = "artifacts/grounding-clustered-analysis-v2.md"


class CorrectionError(ValueError):
    """Raised when the dataset or the v1 rendering does not have the expected form."""


def allocation(root: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Seeds, screen states and per-target counts of the analyzed examples, from the dataset."""

    dataset = {}
    for line in (root / DATASET_PATH).read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            dataset[record["example_id"]] = record
    missing = [row["example_id"] for row in rows if row["example_id"] not in dataset]
    if missing or len(dataset) != len(rows):
        raise CorrectionError(f"dataset and results cover different examples: {missing[:3]}")
    examples = [dataset[row["example_id"]] for row in rows]
    for row, example in zip(rows, examples, strict=True):
        if example["target_id"] != row["target_id"]:
            raise CorrectionError(f"target differs for {row['example_id']}")
    seeds = Counter(example["task_seed"] for example in examples)
    states = Counter(example["screen_state"] for example in examples)
    targets = Counter(example["target_id"] for example in examples)
    return {
        "source": DATASET_PATH,
        "example_count": len(examples),
        "task_seed_count": len(seeds),
        "screen_states": sorted(states),
        "examples_per_task_seed": sorted(set(seeds.values())),
        "examples_per_screen_state": sorted(set(states.values())),
        "target_count": len(targets),
        "examples_per_target": sorted(set(targets.values())),
        "task_seeds_per_target": sorted(
            {len({e["task_seed"] for e in examples if e["target_id"] == t}) for t in targets}
        ),
        "screen_states_per_target": sorted(
            {len({e["screen_state"] for e in examples if e["target_id"] == t}) for t in targets}
        ),
    }


def correct(result: dict[str, Any], alloc: dict[str, Any]) -> dict[str, Any]:
    if alloc["task_seeds_per_target"] != alloc["examples_per_target"] or alloc[
        "screen_states_per_target"
    ] != [1]:
        raise CorrectionError(f"unexpected allocation: {alloc}")
    corrected = copy.deepcopy(result)
    corrected["schema_version"] = SCHEMA_VERSION
    corrected["inputs"] = [*result["inputs"], DATASET_PATH]
    corrected["allocation"] = alloc
    corrected["correction"] = {
        "corrects": v1.JSON_OUTPUT,
        "change": (
            "v1 wrote '10 target controls x 10 seeds', which reads as 10 seeds in all. Each "
            f"target appears on {alloc['task_seeds_per_target'][0]} distinct task seeds, always in "
            f"one screen state, and the set spans {alloc['task_seed_count']} task seeds x "
            f"{len(alloc['screen_states'])} screen states. Only that wording changed."
        ),
        "numbers_unchanged": True,
    }
    return corrected


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise CorrectionError(f"expected one occurrence of {old!r} in the v1 rendering")
    return text.replace(old, new)


def render_markdown(result: dict[str, Any]) -> str:
    """The v1 rendering with the corrected allocation and a correction notice."""

    alloc, c = result["allocation"], result["counts"]
    per_target = result["per_target"][0]["example_count"]
    text = v1.render_markdown(result)
    text = _replace_once(
        text,
        "# Grounding v1 clustered-analysis supplement\n",
        "# Grounding v1 clustered-analysis supplement, v2\n\n"
        f"**Correction.** {result['correction']['change']} The allocation is read from "
        f"`{alloc['source']}`. Every number below equals `{v1.MD_OUTPUT}`, which is left "
        "unchanged.\n",
    )
    text = _replace_once(
        text,
        "`artifacts/grounding-clustered-analysis-v1.json`",
        f"`{JSON_OUTPUT}`",
    )
    text = _replace_once(
        text,
        "Generated by `scripts/generate_grounding_clustered_analysis.py`",
        "Generated by `scripts/generate_grounding_clustered_analysis_v2.py`",
    )
    text = _replace_once(
        text,
        f"({c['target_count']} target controls x {per_target} seeds)",
        f"({c['target_count']} target controls x {per_target} task seeds each, every target in "
        f"one screen state; {alloc['task_seed_count']} task seeds x "
        f"{len(alloc['screen_states'])} screen states in all)",
    )
    return text


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def build(root: Path) -> dict[str, Any]:
    rows = v1.load_rows(root)
    return correct(v1.analyze(rows), allocation(root, rows))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    result = build(args.repository_root)
    out = args.output_dir or args.repository_root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    (out / Path(JSON_OUTPUT).name).write_text(serialize(result), encoding="utf-8")
    (out / Path(MD_OUTPUT).name).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
