"""Recompute README measurements from stored evidence and reject unchecked numeric claims."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pixelgym.tasks.vendor_form import generator, osworld_task

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str) -> Any:
    return json.loads((ROOT / "artifacts" / relative).read_text(encoding="utf-8"))


def _rows(relative: str) -> list[dict[str, Any]]:
    path = ROOT / "artifacts" / relative
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _results() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Results\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(re.sub(r"\]\([^)]+\)", "]", section).split())


def _raw_predictions(filename: str) -> dict[str, dict[str, Any]]:
    raw = [r for r in _rows(filename) if r["condition"] == "raw"]
    by_id = {r["example_id"]: r for r in raw}
    assert raw and len(by_id) == len(raw), filename
    return by_id


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
    yield (
        f"Across {sequences} seeded sequences and {actions:,} actions, the in-process form and "
        "Chromium agreed on form state and submissions after every action."
    )

    guest = [
        row
        for batch in ("a", "b")
        for row in _json(f"osworld-reset-determinism-2026-11-15-{batch}/results.json")["per_seed"]
    ]
    assert len({row["seed"] for row in guest}) == len(guest), "guest reset batches repeat a seed"
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
    results = _json("grounding-results.json")
    yield (
        f"`{results['model']}` placed raw coordinates inside the target on "
        f"{counts['raw_correct']}/{total} screenshots"
    )
    yield f"correct numbered mark on {counts['marks_correct']}/{total}"
    low, high = clustered["target_clustered_bootstrap_95_ci_percentage_points"]
    confidence = clustered["method"]["confidence_level"]
    yield (
        f"+{clustered['delta_percentage_points']:.0f} points, with a target-clustered "
        f"{confidence:.0%} interval of [+{low:.0f}, +{high:.0f}]"
    )
    allocation = clustered["allocation"]
    assert allocation["screen_states_per_target"] == [1]
    yield (
        f"The interval resamples {allocation['target_count']} controls. "
        "Each appeared in only one screen state."
    )
    marks = results["set_of_marks"]
    yield f"Proposal coverage was {marks['proposal_covered_count']}/{marks['proposal_total_count']}"
    split = _json("grounding-ambiguity-split-v1.json")["counts"]
    yield (
        f"Of the {split['raw_misses']} raw misses, {split['request_card_same_row']} landed on the "
        "matching row of the read-only request card."
    )

    original = _raw_predictions("grounding-predictions.jsonl")
    haiku = _raw_predictions("grounding-v3a-scored-predictions-claude-haiku-4.5.jsonl")
    assert original.keys() == haiku.keys() and len(original) == total
    assert {r["model"] for r in haiku.values()} == {"claude-haiku-4-5-20251001"}
    for example_id, row in original.items():
        for field in ("image_sha256", "prompt_sha256"):
            assert row[field] and row[field] == haiku[example_id][field], (example_id, field)
    correct = sum(r["correct"] for r in haiku.values())
    yield (
        f"Claude Haiku 4.5 scored {correct}/{len(haiku)} with raw coordinates on the same "
        "screenshots. The recorded raw prompts matched across runs."
    )

    training = _json("bc-experiment-v2/results.json")["runs"]
    episode_records = [run["closed_loop"]["per_episode"] for run in training]
    successes = sum(episode["success"] for records in episode_records for episode in records)
    episodes = sum(len(records) for records in episode_records)
    n_runs = len(training)
    yield f"{successes}/{episodes} held-out episode evaluations across {n_runs} training runs"
    diagnoses = _json("bc-diagnosis-v1/diagnosis.json")["runs"]
    largest = max(run["demo_episodes"] for run in training)
    for run, records in zip(training, episode_records, strict=True):
        if run["demo_episodes"] != largest:
            continue
        name = run["name"]
        assert name in diagnoses, f"missing BC diagnosis for {name}"
        diagnosed = diagnoses[name]["first_wrong_action_per_episode"]
        assert sorted(row["seed"] for row in diagnosed) == sorted(row["seed"] for row in records), (
            f"BC diagnosis episode seeds differ for {name}"
        )
        for row in diagnosed:
            first = row["first_wrong"]
            assert first and first["kind"] == "key" and first["field"] == "company_name", (
                f"unexpected first error for {name}, seed {row['seed']}"
            )
    yield (
        "In every largest-training-set run, the first wrong action in every test episode "
        "was a company-name keystroke."
    )

    resume = _json("platform/seed-policy-fanout-evidence-v1.json")["resume_run"]
    assert resume["failed_command_exit_code"] != 0 and resume["resume_command_exit_code"] == 0
    ledger = resume["provider_ledger"]
    yield f"{ledger['attempts']} attempts and {ledger['unique_request_ids']} unique request IDs"


def _unchecked_numbers(section: str) -> list[str]:
    for claim in _claims():
        assert claim in section, claim
        section = section.replace(claim, " ", 1)
    return re.findall(r"\d+", section)


def test_results_match_stored_evidence() -> None:
    assert _unchecked_numbers(_results()) == []


def test_uncovered_number_is_reported() -> None:
    assert _unchecked_numbers(_results() + " another 4242 actions") == ["4242"]


def test_repeated_numeric_claim_is_reported() -> None:
    claim = next(claim for claim in _claims() if claim.startswith("Across "))
    assert _unchecked_numbers(f"{_results()} {claim}") == re.findall(r"\d+", claim)


def test_results_reject_a_different_request_card_row() -> None:
    changed = _results().replace("matching row", "unrelated row", 1)
    with pytest.raises(AssertionError, match="matching row"):
        _unchecked_numbers(changed)


def test_frozen_instructions_have_fixed_boxes_and_marks_across_seeds() -> None:
    overlays = {row["example_id"]: row for row in _rows("grounding-overlays.jsonl")}
    labels: dict[str, set[tuple[tuple[int, ...], int]]] = {}
    seeds: dict[str, set[int]] = {}
    for example in _rows("grounding-dataset.jsonl"):
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
    evidence_path = "osworld-guest-check-2026-11-12-248097d/results.json"
    index = (ROOT / "docs/evidence-index.md").read_text(encoding="utf-8")
    (current_row,) = (
        line
        for line in index.splitlines()
        if line.startswith("| Does the current guest bundle run on the pinned OSWorld guest? |")
    )
    results_links = re.findall(r"\]\(([^)]+/results\.json)\)", current_row)
    assert results_links == [f"../artifacts/{evidence_path}"]

    current = _json(evidence_path)
    assert (
        osworld_task.build_guest_bundle(generator.generate_task(7), tmp_path / "b.zip")
        == (current["provenance"]["guest_bundle_sha256_seed7"])
    )
