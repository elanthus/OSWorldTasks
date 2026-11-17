"""Recompute README measurements from stored evidence and reject unchecked numeric claims."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pixelgym.tasks.vendor_form import generator, osworld_task

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str) -> Any:
    return json.loads((ROOT / "artifacts" / relative).read_text(encoding="utf-8"))


def _results() -> str:
    section = (ROOT / "README.md").read_text().split("## Results\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(re.sub(r"\]\([^)]+\)", "]", section).split())


def _claims() -> Iterator[str]:
    differential = _json("browser-differential-v1/evidence.json")
    barrier = differential["barrier"]
    (off,) = barrier["negative_control_barrier_off"]["in_step_reads"]
    (on,) = barrier["positive_control_barrier_on"]["in_step_reads"]
    yield f"saw {off} submissions on the action step without the barrier and {on} with it"
    golden = barrier["golden_under_submit_latency"]
    (step,) = {row["steps"] for row in golden}
    assert all(row["positive_reward_steps"] == [step] for row in golden)
    yield f"only reward on step {step}"
    runs = differential["runs"]
    assert all(row["divergence_count"] == 0 for row in runs)
    sequences = sum(row["sequence_count"] for row in runs)
    actions = sum(row["action_count"] for row in runs)
    yield f"{sequences} seeded sequences and {actions:,} actions"

    guest = [
        row
        for batch in ("a", "b")
        for row in _json(f"osworld-reset-determinism-2026-11-15-{batch}/results.json")["per_seed"]
    ]
    assert all(len(row["resets"]) == 5 and not row["reset_errors"] for row in guest)
    semantic = sum(
        len(row["distinct_task_sha256"]) == len(row["distinct_form_state_sha256"]) == 1
        for row in guest
    )
    visual = sum(len(row["distinct_frame_sha256"]) == 1 for row in guest)
    yield f"task and form-state hashes repeated on {semantic}/{len(guest)} seeds across five resets"
    yield f"Screenshots were byte-identical on {visual}/{len(guest)} seeds"

    def chromium_frames(host: str) -> dict[int, str]:
        frames = {}
        for row in _json(f"chromium-reset-determinism-v1/{host}.json")["per_seed"]:
            (digest,) = row["distinct_frame_sha256"]
            frames[row["seed"]] = digest
        return frames

    mac, linux = chromium_frames("macos-arm64"), chromium_frames("linux-ci")
    assert mac.keys() == linux.keys()
    differences = sum(mac[seed] != linux[seed] for seed in mac)
    yield f"differed between macOS and Linux on {differences}/{len(mac)} seeds"

    clustered = _json("grounding-clustered-analysis-v2.json")
    counts = clustered["counts"]
    total = counts["example_count"]
    yield f"inside the target on {counts['raw_correct']}/{total} screenshots"
    yield f"correct numbered mark on {counts['marks_correct']}/{total}"
    low, high = clustered["target_clustered_bootstrap_95_ci_percentage_points"]
    confidence = clustered["method"]["confidence_level"]
    yield (
        f"+{clustered['delta_percentage_points']:.0f} points, with a target-clustered "
        f"{confidence:.0%} interval of [+{low:.0f}, +{high:.0f}]"
    )
    marks = _json("grounding-results.json")["set_of_marks"]
    yield f"Proposal coverage was {marks['proposal_covered_count']}/{marks['proposal_total_count']}"
    split = _json("grounding-ambiguity-split-v1.json")["counts"]
    yield f"Of the {split['raw_misses']} raw misses, {split['request_card_same_row']} landed"

    training = _json("bc-experiment-v2/results.json")["runs"]
    successes = sum(run["closed_loop"]["successes"] for run in training)
    episodes = sum(run["closed_loop"]["episodes"] for run in training)
    n_runs = len(training)
    yield f"{successes}/{episodes} held-out episode evaluations across {n_runs} training runs"
    diagnosis = _json("bc-diagnosis-v1/diagnosis.json")["runs"].values()
    largest = max(run["demo_episodes"] for run in training)
    for run in diagnosis:
        if run["demo_episodes"] == largest:
            errors = run["first_wrong_action"]
            assert errors["by_field_for_keys"] == {"company_name": errors["episodes"]}

    resume = _json("platform/seed-policy-fanout-evidence-v1.json")["resume_run"]
    assert resume["failed_command_exit_code"] != 0 and resume["resume_command_exit_code"] == 0
    ledger = resume["provider_ledger"]
    yield f"{ledger['attempts']} attempts and {ledger['unique_request_ids']} unique request IDs"


def _unchecked_numbers(section: str) -> list[str]:
    for claim in _claims():
        assert claim in section, claim
        section = section.replace(claim, "")
    # The model alias is a recorded identifier.
    return re.findall(r"\d+", section.replace("`gpt-5.4-mini`", ""))


def test_results_match_stored_evidence() -> None:
    assert _unchecked_numbers(_results()) == []


def test_uncovered_number_is_reported() -> None:
    assert _unchecked_numbers(_results() + " another 4242 actions") == ["4242"]


def test_frozen_instructions_have_fixed_boxes_and_marks_across_seeds() -> None:
    def rows(name: str) -> list[dict[str, Any]]:
        path = ROOT / "artifacts" / f"grounding-{name}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    overlays = {row["example_id"]: row for row in rows("overlays")}
    labels: dict[str, set[tuple[tuple[int, ...], int]]] = {}
    seeds: dict[str, set[int]] = {}
    for example in rows("dataset"):
        instruction = example["target"]
        (mark,) = (
            m
            for m in overlays[example["example_id"]]["marks"]
            if m["semantic_id"] == example["target_id"]
        )
        labels.setdefault(instruction, set()).add((tuple(example["bbox"]), mark["mark_id"]))
        seeds.setdefault(instruction, set()).add(example["task_seed"])
    assert labels and all(len(values) == 1 for values in labels.values())
    assert all(len(values) > 1 for values in seeds.values())


def test_evidence_index_current_guest_bundle_matches_build(tmp_path: Path) -> None:
    current = _json("osworld-guest-check-2026-11-12-248097d/results.json")
    assert (
        osworld_task.build_guest_bundle(generator.generate_task(7), tmp_path / "b.zip")
        == (current["provenance"]["guest_bundle_sha256_seed7"])
    )
