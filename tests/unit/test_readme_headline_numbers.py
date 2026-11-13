"""Every number in the README's Results section is read from the artifact that bullet cites.

Each claim below is rebuilt from stored evidence and must appear verbatim in the section. A second
test extracts every numeric token from the section's prose and requires each to be covered by one
of those claims, or by the short allowlist of non-measured numbers (model names, the confidence
level, and git provenance), so a new or edited number cannot appear without a check.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pixelgym.tasks.vendor_form import generator, osworld_task

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
WORDS = {3: "three", 5: "Five", 10: "ten"}
# A number in prose; digits glued to a letter, such as the version name "v5", are names.
NUMBER = r"(?<![A-Za-z])\d+(?:[./]\d+)?"

# Numbers in the section that are not measurements: model names, the confidence level (checked
# against the analysis method below), and the git commit date of the reset evidence.
NON_MEASURED = (
    "Claude Haiku 4.5",
    "95% CI",
    "on 2026-09-06",
)


def _json(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _results_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Results")
    end = text.index("What these results do not show", start)
    return " ".join(text[start:end].split())


def _prose(section: str) -> str:
    """The section without link targets and inline code, where numbers are paths or hashes."""

    section = re.sub(r"\]\([^)]*\)", "]", section)
    return re.sub(r"`[^`]*`", "", section)


def _claims() -> Iterator[str]:
    reset = _json("artifacts/day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json")
    records = reset["records"]
    assert all(r["differing_pixel_count"] == 0 and r["max_per_channel_delta"] == 0 for r in records)
    assert all("seed-7-" in r["screenshot_path"] for r in records)
    width, height = reset["backend_metadata"]["screen_size"]
    yield (
        f"{WORDS[len(records)]} real OSWorld resets of seed 7 were bitwise identical at "
        f"{width}×{height} on one host"
    )
    yield f"guest bundle `{reset['backend_metadata']['task_bundle_sha256'][:8]}…`"

    current = _json("artifacts/osworld-guest-check-2026-11-12-248097d/results.json")
    provenance = current["provenance"]
    bundle = provenance["guest_bundle_sha256_seed7"]
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = osworld_task.build_guest_bundle(generator.generate_task(7), Path(tmp) / "b.zip")
    assert rebuilt == bundle, "the README calls this bundle current; the code no longer builds it"
    yield f"the current guest bundle is `{bundle[:8]}…`"
    yield (
        f"with the current guest bundle `{bundle[:8]}…` (recorded at "
        f"`{provenance['git_revision'][:7]}`)"
    )
    resets = current["resets"]["records"]
    assert all(r["reset_returned"] and r["acknowledged_after_reset"] == 0 for r in resets)
    yield f"{WORDS[len(resets)]} resets each reported 0 setup acknowledgements"
    summary = current["golden"]["summary"]
    assert summary["positive_reward_count"] == 1
    assert summary["first_positive_reward_step"] == summary["terminal_step"]
    yield "the golden trajectory paid its only reward at its last step"
    cases = current["cases"]["records"].values()
    assert all(case["match"] is True for case in cases)
    yield f"{WORDS[len(cases)]} input sequences submitted the same values as the fake"
    earlier = _json("artifacts/osworld-guest-check-2026-11-12/provenance.json")
    yield f"An earlier run on bundle `{earlier['guest_bundle_sha256_recorded'][:8]}…` is retained"

    throughput = _json("artifacts/throughput-v2/fake-throughput.json")
    workloads = throughput["workloads"]

    def median(workload: str, renderer: str) -> int:
        return round(workloads[workload][renderer]["steps_per_second"]["median"])

    frames = throughput["byte_identity"]
    assert all(row["mismatched_frames"] == 0 for row in frames)
    noop_share = workloads["random"]["action_types"]["noop_share"]
    yield (
        f"replays the golden trajectory at {median('golden', 'static_layer')} steps/s, up from "
        f"{median('golden', 'reference_single_pass')} with the earlier single-pass renderer, and "
        f"runs a random policy at {median('random', 'static_layer')} steps/s (up from "
        f"{median('random', 'reference_single_pass')}), of whose steps {noop_share:.1%} are NOOPs"
    )
    yield (
        f"all {sum(row['frames_compared'] for row in frames)} compared frames were "
        f"byte-identical (recorded at `{throughput['git_revision'][:7]}`"
    )

    parity = _json("artifacts/browser-differential-v1/evidence.json")
    runs = parity["runs"]
    barrier = parity["barrier"]
    (off,) = barrier["negative_control_barrier_off"]["in_step_reads"]
    (on,) = barrier["positive_control_barrier_on"]["in_step_reads"]
    yield (
        f"replays {sum(r['sequence_count'] for r in runs)} seeded action sequences "
        f"({sum(r['action_count'] for r in runs)} actions) on the fake and on the real page in "
        f"Chromium, with {sum(r['divergence_count'] for r in runs)} divergent sequences"
    )
    yield (
        f"read {off} submissions in the step without the input-acknowledgement barrier and {on} "
        f"with it (recorded at `{parity['git_revision'][:7]}`"
    )

    clustered = _json("artifacts/grounding-clustered-analysis-v2.json")
    allocation, counts = clustered["allocation"], clustered["counts"]
    (per_seed,) = set(allocation["examples_per_task_seed"])
    (per_target,) = set(allocation["examples_per_target"])
    total = counts["example_count"]
    yield (
        f"On {total} paired screenshots ({allocation['task_seed_count']} task seeds × {per_seed} "
        f"screen states; each of {allocation['target_count']} target controls appears "
        f"{per_target} times)"
    )
    report = (ROOT / "artifacts/grounding-report-v2.md").read_text(encoding="utf-8")
    marks = re.search(r"selection among (\d+) labelled candidates", report)
    assert marks is not None
    yield (
        f"scored {counts['raw_correct']}/{total} with raw coordinates and "
        f"{counts['marks_correct']}/{total} when choosing among {marks.group(1)} labelled marks"
    )
    low, high = clustered["target_clustered_bootstrap_95_ci_percentage_points"]
    assert clustered["method"]["confidence_level"] == 0.95
    yield (
        f"+{clustered['delta_percentage_points']:.0f} points, target-clustered 95% CI "
        f"[+{low:.0f}, +{high:.0f}]"
    )
    marks_summary = _json("artifacts/grounding-results.json")["set_of_marks"]
    yield (
        f"Proposal coverage, reported separately from that selection accuracy, was "
        f"{marks_summary['proposal_covered_count']}/{marks_summary['proposal_total_count']}"
    )

    split = _json("artifacts/grounding-ambiguity-split-v1.json")
    split_counts, by_target = split["counts"], split["request_card_same_row_by_target"]
    yield (
        f"{split_counts['request_card_same_row']} of {split_counts['raw_misses']}, land on the "
        "read-only request card"
    )
    yield (
        f"(company name {by_target['company_name']}, contact phone {by_target['contact_phone']}, "
        f"contact email {by_target['contact_email']})"
    )
    forgiving = split["forgiving"]
    f_low, f_high = forgiving["target_clustered_bootstrap_95_ci_percentage_points"]
    yield (
        f"raw {split_counts['forgiving_raw_correct']}/{split_counts['example_count']} and "
        f"+{forgiving['delta_percentage_points']:.0f} points, target-clustered 95% CI "
        f"[+{f_low:.0f}, +{f_high:.0f}]"
    )

    chance = _json("artifacts/grounding-v5-stateless-chance-ceiling-v1.json")
    order = ("historical", "replication", "parser_sensitivity")
    campaigns = [chance["campaigns"][name] for name in order]
    episodes = chance["episodes_per_arm"]
    assert all(c["episodes_per_arm"] == episodes for c in campaigns)
    history = [c["history_successes"] for c in campaigns]
    stateless = [c["stateless_successes"] for c in campaigns]
    yield (
        f"on {episodes} multi-screen episodes succeeded {history[0]}, {history[1]}, and "
        f"{history[2]} times with its screenshot history and {stateless[0]}, {stateless[1]}, and "
        f"{stateless[2]} times without it"
    )
    uniform = chance["uniform_guess"]
    yield (
        f"an otherwise-perfect guesser {uniform['success_probability']}, about "
        f"{round(uniform['expected_successes'])} of {episodes}"
    )


def test_every_headline_claim_matches_its_artifact() -> None:
    section = _results_section()
    missing = [claim for claim in _claims() if claim not in section]
    assert not missing, missing


def test_every_number_in_the_section_is_covered_by_a_checked_claim() -> None:
    section = _results_section()
    covered = _prose(section)
    for phrase in (*_claims(), *NON_MEASURED):
        assert phrase in section, phrase
        covered = covered.replace(_prose(phrase), " ")
    leftover = re.findall(NUMBER, covered)
    assert not leftover, leftover


def test_uncovered_number_is_reported() -> None:
    # Negative control: a number added to the section without a claim is caught.
    section = _results_section() + " The fake backend also runs 4242 steps."
    covered = _prose(section)
    for phrase in (*_claims(), *NON_MEASURED):
        covered = covered.replace(_prose(phrase), " ")
    assert re.findall(NUMBER, covered) == ["4242"]
