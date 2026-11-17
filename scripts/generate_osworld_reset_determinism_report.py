#!/usr/bin/env python3
"""Generate the guest reset-determinism report from stored ``results.json`` files only.

Reads one or more run folders written by ``scripts/osworld_reset_determinism.py`` and writes a
Markdown report. It boots nothing, reruns nothing, and applies no tolerance. Saved PNG frames are
decoded only to check that each one matches the frame hash the driver recorded for it.

    .venv/bin/python scripts/generate_osworld_reset_determinism_report.py \\
        artifacts/osworld-reset-determinism-2026-11-15-a \\
        artifacts/osworld-reset-determinism-2026-11-15-b \\
        --output artifacts/osworld-reset-determinism-2026-11-15-report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_chromium_reset_determinism import frame_sha256

SCHEMA_VERSION = "pixelgym-osworld-reset-determinism-v1"

DEFINITIONS = (
    "- **Semantic determinism**: the reset task record and the privileged application state have "
    "the same SHA-256 on every reset of a seed. Measured.\n"
    "- **Bitwise visual determinism**: the RGB frame bytes returned by reset have the same "
    "SHA-256 on every reset of a seed. Measured.\n"
    "- **Perceptual visual stability**: a tolerance-based similarity. Not computed; no tolerance "
    "is applied to any number below."
)

METADATA_FIELDS = (
    "upstream_repository",
    "upstream_tag",
    "upstream_commit",
    "release",
    "provider",
    "python_version",
    "screen_size",
    "runtime_image",
    "runtime_image_base",
    "runtime_source_commit",
    "guest_artifact_repository",
    "guest_artifact_tag",
    "guest_artifact_name",
    "guest_artifact_sha256",
    "guest_volume_size_gb",
    "input_acknowledgement_barrier",
)


def load(folder: Path) -> dict[str, Any]:
    evidence = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{folder}: unexpected schema {evidence.get('schema_version')!r}")
    return evidence


def hash_groups(seed_summary: dict[str, Any]) -> list[tuple[str, list[int]]]:
    """Each distinct frame hash with the reset indices that produced it, in first-seen order."""
    groups: dict[str, list[int]] = {}
    for reset in seed_summary.get("resets", []):
        groups.setdefault(reset["frame_sha256"], []).append(reset["reset_index"])
    return list(groups.items())


def saved_frames(folder: Path, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Every saved PNG, and whether it decodes to the hash recorded for that seed and reset."""
    from PIL import Image

    recorded = {
        (s["seed"], r["reset_index"]): r["frame_sha256"]
        for s in evidence["per_seed"]
        for r in s.get("resets", [])
    }
    rows = []
    for path in sorted(folder.glob("seed-*-reset-*.png")):
        _, seed, _, index = path.stem.split("-")
        with Image.open(path) as image:
            digest = frame_sha256(np.asarray(image.convert("RGB"), dtype=np.uint8))
        expected = recorded.get((int(seed), int(index)))
        rows.append(
            {
                "file": path.name,
                "seed": int(seed),
                "reset_index": int(index),
                "matches_recorded_hash": expected is not None and digest == expected,
            }
        )
    return sorted(rows, key=lambda r: (r["seed"], r["reset_index"]))


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _short(digest: str) -> str:
    return f"`{digest[:12]}`"


def render_run(root: Path, folder: Path, ev: dict[str, Any]) -> list[str]:
    p = ev["provenance"]
    meta = ev.get("integration_metadata") or {}
    close = ev.get("provider_close") or {}
    errors = sum(len(s.get("reset_errors", [])) for s in ev["per_seed"])
    completed = sum(len(s.get("resets", [])) for s in ev["per_seed"])
    close_error = "none" if close.get("error") is None else "recorded"
    budget_skips = sum(1 for k in ev["skipped"] if k.get("reason") == "time budget")
    other_skips = len(ev["skipped"]) - budget_skips
    other_skip_text = (
        f", {other_skips} skipped for another reason (listed below)" if other_skips else ""
    )
    lines = [
        f"## Run `{folder.name}`",
        "",
        (
            f"- Started {p['run_started_utc']}; revision `{p['git_revision']}`; worktree clean at "
            f"start: {_yn(p['worktree_clean_at_start'])}."
        ),
        (
            f"- {len(ev['seeds'])} seeds × {ev['resets_per_seed']} resets requested; {completed} "
            f"completed, {errors} failed, {budget_skips} skipped by the "
            f"{ev['budget_minutes']:g}-minute budget{other_skip_text}; "
            f"{ev.get('elapsed_seconds')} s elapsed."
        ),
        (
            f"- Provider close: attempted {_yn(close.get('attempted') is True)}, returned "
            f"{_yn(close.get('returned') is True)}, error {close_error}, "
            f"suppressed close errors {len(close.get('suppressed_close_errors', []))}."
        ),
        (
            f"- Input contract `{p['input_contract']}`; setup acknowledged inputs "
            f"{p['setup_acknowledged_inputs']}."
        ),
        "",
        "Integration metadata as recorded:",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    if not meta:
        lines.append("| (none recorded) | |")
    for field in METADATA_FIELDS:
        if field in meta:
            lines.append(f"| `{field}` | `{meta[field]}` |")
    lines += [
        "",
        (
            "| Seed | Guest bundle SHA-256 | Resets | Distinct frame hashes | "
            "Distinct task hashes | Distinct application-state hashes | Differing frame pairs | "
            "Max differing pixels | Max per-channel delta | Differing bbox (xyxy) | "
            "Reset errors |"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for s in sorted(ev["per_seed"], key=lambda s: ev["seeds"].index(s["seed"])):
        pairs = [d for d in s.get("differing_frame_pairs", []) if d.get("comparable")]
        max_px = max((d["differing_pixel_count"] for d in pairs), default=0)
        max_delta = max((d["max_per_channel_delta"] for d in pairs), default=0)
        boxes = sorted({tuple(d["differing_pixel_bbox_xyxy"]) for d in pairs})
        bbox = "; ".join(str(list(b)) for b in boxes) if boxes else "—"
        bundle = p["guest_bundle_sha256_by_seed"].get(str(s["seed"]), "")
        counts = [
            len(s.get(key, []))
            for key in (
                "resets",
                "distinct_frame_sha256",
                "distinct_task_sha256",
                "distinct_form_state_sha256",
            )
        ]
        pairs_cell = f"{len(s.get('differing_frame_pairs', []))}/{s.get('pair_count', 0)}"
        cells = [s["seed"], _short(bundle), *counts, pairs_cell, max_px, max_delta, bbox]
        cells.append(len(s.get("reset_errors", [])))
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += [
        "",
        "Frame hash per reset (resets sharing a hash are grouped):",
        "",
        "| Seed | Frame hash | Reset indices | Seconds per reset |",
        "|---:|---|---|---|",
    ]
    for s in ev["per_seed"]:
        seconds = {r["reset_index"]: r["seconds"] for r in s.get("resets", [])}
        for digest, indices in hash_groups(s):
            lines.append(
                f"| {s['seed']} | {_short(digest)} | {', '.join(map(str, indices))} | "
                f"{', '.join(str(seconds[i]) for i in indices)} |"
            )
    if ev["skipped"]:
        lines += ["", "Skipped resets:", ""]
        lines += [
            f"- seed {k['seed']} reset {k['reset_index']}: {k['reason']}" for k in ev["skipped"]
        ]
    frames = saved_frames(root / folder, ev)
    lines += [
        "",
        "Saved frames (the driver saves reset 0 and any reset whose hash differs from reset 0):",
        "",
        "| File | Decodes to the recorded hash |",
        "|---|---|",
    ]
    lines += [f"| `{f['file']}` | {_yn(f['matches_recorded_hash'])} |" for f in frames]
    lines.append("")
    return lines


def render(root: Path, folders: list[Path], evidences: list[dict[str, Any]]) -> str:
    failed = sum(len(s.get("reset_errors", [])) for ev in evidences for s in ev["per_seed"])
    providers = (
        "No reset failed, so each run used one provider."
        if failed == 0
        else (
            "A failed reset closes its provider and the next reset starts another, so a run "
            "with a failed reset may have used more than one provider."
        )
    )
    images = [
        (ev.get("integration_metadata") or {}).get("guest_artifact_sha256") for ev in evidences
    ]
    if None in images:
        image_scope = "The guest artifact digest is not recorded for every run"
    elif len(set(images)) == 1:
        image_scope = f"One guest image (`guest_artifact_sha256` `{images[0]}` in every run)"
    else:
        image_scope = (
            f"{len(set(images))} distinct guest artifact digests across runs (see each run's "
            "metadata)"
        )
    lines = [
        "# OSWorld guest reset determinism",
        "",
        "Generated by `scripts/generate_osworld_reset_determinism_report.py` from "
        + ", ".join(f"`{f.as_posix()}/results.json`" for f in folders)
        + ". Each was recorded by `scripts/osworld_reset_determinism.py` through `OSWorldBackend`. "
        + providers,
        "",
        "## Terms",
        "",
        DEFINITIONS,
        "",
    ]
    for folder, ev in zip(folders, evidences, strict=True):
        lines += render_run(root, folder, ev)
    seeds = [s for ev in evidences for s in ev["per_seed"]]

    def single(s: dict[str, Any], key: str) -> bool:
        return len(s.get(key, [])) == 1

    one_frame = sum(1 for s in seeds if single(s, "distinct_frame_sha256"))
    one_state = sum(
        1
        for s in seeds
        if single(s, "distinct_task_sha256") and single(s, "distinct_form_state_sha256")
    )
    closed = sum(1 for ev in evidences if ev.get("provider_closed") is True)
    lines += [
        "## Totals",
        "",
        f"- Seeds with one frame hash across all completed resets: {one_frame} of {len(seeds)}.",
        f"- Seeds with one task hash and one application-state hash: {one_state} of {len(seeds)}.",
        f"- Runs whose provider close returned without error: {closed} of {len(evidences)}.",
        "",
        "## Scope",
        "",
        (
            f"{image_scope}, the bundle digests listed above. The results do not record the "
            "host, so this report does not establish that the runs shared one. This report does "
            "not compare hosts or guests, does not compute perceptual "
            "similarity, and does not state why any frame differs; the saved PNGs are the "
            "evidence for that."
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="+", help="run folders holding results.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    root = args.repository_root
    folders = [f.relative_to(root) if f.is_relative_to(root) else f for f in args.runs]
    evidences = [load(root / f) for f in folders]
    output = args.output if args.output.is_absolute() else root / args.output
    output.write_text(render(root, folders, evidences), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
