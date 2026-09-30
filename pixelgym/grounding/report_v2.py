"""Grounding report v2: data-derived prose over frozen v1 evidence.

Renders ``artifacts/grounding-report-v2.md`` and its provenance record from stored evidence
only. No model, network, or evaluation call is made, and the frozen v1 rendering path in
``pixelgym.grounding.report`` is neither used nor changed. Every count, interval, and
descriptive sentence about exclusions and error review is computed from the files listed in
``INPUT_PATHS``; none is a literal.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_VERSION = "pixelgym-grounding-report-v2"
PROVENANCE_SCHEMA = "pixelgym-grounding-report-provenance-v2"
GENERATOR_PATH = "scripts/generate_grounding_report_v2.py"
VERIFIER_PATH = "scripts/verify_grounding_report_v2.py"
MODULE_PATH = "pixelgym/grounding/report_v2.py"
REPORT_PATH = "artifacts/grounding-report-v2.md"
PROVENANCE_PATH = "artifacts/grounding-report-provenance-v2.json"
RESULTS_PATH = "artifacts/grounding-results.json"
PREDICTIONS_PATH = "artifacts/grounding-predictions.jsonl"
CLUSTERED_PATH = "artifacts/grounding-clustered-analysis-v1.json"
ERROR_REVIEW_PATH = "artifacts/grounding-error-review.jsonl"
DECISIONS_PATH = "artifacts/grounding-error-review-decisions.json"
OVERLAYS_PATH = "artifacts/grounding-overlays.jsonl"
INPUT_PATHS = (
    RESULTS_PATH,
    PREDICTIONS_PATH,
    CLUSTERED_PATH,
    ERROR_REVIEW_PATH,
    DECISIONS_PATH,
    OVERLAYS_PATH,
)
PROTOCOL_PATH = "artifacts/grounding-protocol.md"
V1_REPORT_PATH = "artifacts/grounding-report.md"
V1_PROVENANCE_PATH = "artifacts/grounding-report-provenance-v1.json"
HISTORICAL_V3_REPORT_PATH = "artifacts/grounding-v3-haiku-gemini-report.md"
# Quoted from the cited historical v3 report (hashed in the provenance); the only figure in the
# v2 report that is not derived from INPUT_PATHS. The test suite checks it against that file.
HISTORICAL_HAIKU_RAW_TEXT = "100/100"
CITED_PATHS = (PROTOCOL_PATH, V1_REPORT_PATH, V1_PROVENANCE_PATH, HISTORICAL_V3_REPORT_PATH)
APPLIES_FROM = {
    "pull_request": "elanthus/OSWorldTasks#232",
    "branch": "cleanup/wp19-grounding-report-v2",
}
APPLIES_FROM_NOTE = (
    "The first main revision containing this report is the merge commit of that pull request, "
    "resolvable from GitHub."
)


class GroundingReportV2Error(ValueError):
    """Raised when the frozen inputs are inconsistent with each other."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_inputs(root: Path) -> dict[str, Any]:
    """Read the frozen inputs; nothing else is read to build the report text."""
    return {
        "results": json.loads((root / RESULTS_PATH).read_text(encoding="utf-8")),
        "predictions": _load_jsonl(root / PREDICTIONS_PATH),
        "clustered": json.loads((root / CLUSTERED_PATH).read_text(encoding="utf-8")),
        "error_review": _load_jsonl(root / ERROR_REVIEW_PATH),
        "decisions": json.loads((root / DECISIONS_PATH).read_text(encoding="utf-8")),
        "overlays": _load_jsonl(root / OVERLAYS_PATH),
    }


def _pp(value: float) -> str:
    return f"{value:+.1f}"


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def _counts_text(counter: Counter[str]) -> str:
    return ", ".join(f"{count} `{key}`" for key, count in sorted(counter.items()))


def _check_consistency(inputs: dict[str, Any]) -> None:
    results = inputs["results"]
    clustered = inputs["clustered"]
    per_example = results["per_example"]
    by_key = {(row["example_id"], row["condition"]): row for row in inputs["predictions"]}
    if len(by_key) != len(inputs["predictions"]):
        raise GroundingReportV2Error("duplicate (example_id, condition) in predictions")
    if len(by_key) != results["collection"]["condition_record_count"]:
        raise GroundingReportV2Error("prediction record count differs from results")
    for row in per_example:
        for condition in ("raw", "marks"):
            record = by_key.get((row["example_id"], condition))
            if record is None or record["correct"] != row[condition]["correct"]:
                raise GroundingReportV2Error(
                    f"predictions disagree with results for {row['example_id']}/{condition}"
                )
    if clustered["counts"]["example_count"] != len(per_example):
        raise GroundingReportV2Error("clustered supplement example count differs from results")
    if clustered["delta_percentage_points"] != results["paired"]["delta_percentage_points"]:
        raise GroundingReportV2Error("clustered supplement delta differs from results")
    if (
        clustered["example_level_bootstrap_95_ci_percentage_points"]
        != results["paired"]["bootstrap_95_ci_percentage_points"]
    ):
        raise GroundingReportV2Error("clustered supplement example-level CI differs from results")
    if {row["example_id"] for row in inputs["overlays"]} != {
        row["example_id"] for row in per_example
    }:
        raise GroundingReportV2Error("overlay example IDs differ from results")
    review_counts: Counter[str] = Counter()
    for review in inputs["error_review"]:
        review_counts.update(review["categories"])
    if dict(review_counts) != results["error_taxonomy"]["category_counts"]:
        raise GroundingReportV2Error("error-review categories differ from results taxonomy")
    if len(inputs["error_review"]) != results["error_taxonomy"]["error_record_count"]:
        raise GroundingReportV2Error("error-review record count differs from results")
    assigned: Counter[str] = Counter(
        {key: len(value) for key, value in inputs["decisions"]["category_assignments"].items()}
    )
    if assigned != review_counts:
        raise GroundingReportV2Error("review decisions differ from error-review categories")


def exclusion_sentence(results: dict[str, Any]) -> str:
    """Describe exclusions from the stored counts, never from a literal."""
    excluded = int(results["collection"]["excluded_example_count"])
    retained = len(results["per_example"])
    noun = "example was" if excluded == 1 else "examples were"
    return (
        f"{excluded} {noun} excluded (n={retained} of {retained + excluded} retained), "
        "according to `collection.excluded_example_count` and the `per_example` rows in the "
        "stored results."
    )


def error_review_paragraph(error_review: list[dict[str, Any]], decisions: dict[str, Any]) -> str:
    """Describe the error review from the stored review artifacts' own fields."""
    status = Counter(str(row.get("review_status", "missing")) for row in error_review)
    conditions = Counter(str(row["condition"]) for row in error_review)
    reviewers = {str(row["reviewer"]) for row in error_review if row.get("reviewer")}
    if decisions.get("reviewer"):
        reviewers.add(str(decisions["reviewer"]))
    reviewer_text = (
        "Reviewer recorded: " + ", ".join(f"`{name}`" for name in sorted(reviewers)) + "."
        if reviewers
        else "The review artifacts do not record a reviewer identity."
    )
    method = decisions.get("review_method")
    method_text = (
        f'Recorded review method: "{method}"' if method else "No review method is recorded."
    )
    return (
        f"The error-review artifact holds {len(error_review)} error records "
        f"(by condition: {_counts_text(conditions)}; by `review_status`: "
        f"{_counts_text(status)}). {method_text} {reviewer_text} The decisions file assigns "
        f"records to {len(decisions['category_assignments'])} categories; categories are "
        "non-exclusive, so their counts can sum above the number of error records."
    )


def _candidate_text(overlays: list[dict[str, Any]]) -> str:
    sizes = {len(row["marks"]) for row in overlays}
    if len(sizes) == 1:
        return f"{next(iter(sizes))} labelled candidates on every example"
    return f"between {min(sizes)} and {max(sizes)} labelled candidates per example"


def render_report_v2(inputs: dict[str, Any]) -> str:
    """Render the v2 Markdown report from the loaded inputs."""
    _check_consistency(inputs)
    results = inputs["results"]
    clustered = inputs["clustered"]
    decisions = inputs["decisions"]
    raw = results["conditions"]["raw"]
    marks = results["conditions"]["marks"]
    paired = results["paired"]
    som = results["set_of_marks"]
    counts = clustered["counts"]
    per_target_size = clustered["per_target"][0]["example_count"]
    ex_low, ex_high = clustered["example_level_bootstrap_95_ci_percentage_points"]
    cl_low, cl_high = clustered["target_clustered_bootstrap_95_ci_percentage_points"]
    sign = clustered["target_sign_test"]
    cp = clustered["clopper_pearson_95_ci_marks_only_proportion"]
    tokens = clustered["harness_input_tokens"]
    lines = [
        "# PixelGym Grounding Experiment (report v2)",
        "",
        (
            f"This is `{REPORT_VERSION}`. It re-renders the frozen v1 evidence with prose derived "
            "from the stored data and with both bootstrap intervals. It makes no model or "
            f"evaluation call. The frozen original report, [`{V1_REPORT_PATH}`]"
            "(grounding-report.md), is retained unchanged."
        ),
        "",
        f"- Protocol: `{results['protocol_version']}`",
        f"- Prompt: `{results['prompt_version']}`",
        f"- Provider/model: `{results['provider']}` / `{results['model']}`",
        (
            f"- Sample: {counts['example_count']} paired examples ({counts['target_count']} "
            f"target controls x {per_target_size} seeds), "
            f"{results['collection']['condition_record_count']} condition records"
        ),
        "",
        "## Main result",
        "",
        (
            f"Raw-coordinate accuracy was {raw['correct_count']}/{raw['record_count']} "
            f"({_percent(raw['accuracy'])}). Set-of-marks accuracy was "
            f"{marks['correct_count']}/{marks['record_count']} ({_percent(marks['accuracy'])}). "
            f"The paired difference (marks minus raw) was "
            f"{_pp(paired['delta_percentage_points'])} percentage points."
        ),
        "",
        (
            "Two 95% percentile-bootstrap intervals for that difference are reported side by "
            "side. They use different units of analysis, and neither is the single result:"
        ),
        "",
        "| Interval | Unit of analysis | n | 95% CI (pp) |",
        "|---|---|---:|---|",
        (
            f"| Example-level bootstrap | example | {counts['example_count']} | "
            f"[{_pp(ex_low)}, {_pp(ex_high)}] |"
        ),
        (
            f"| Target-clustered bootstrap | target control | {counts['target_count']} | "
            f"[{_pp(cl_low)}, {_pp(cl_high)}] |"
        ),
        "",
        (
            f"- Target-level exact sign test (unit: target control): "
            f"{sign['targets_marks_better']} targets favour marks, "
            f"{sign['targets_raw_better']} favour raw, {sign['targets_tied']} tied; two-sided "
            f"p = {sign['two_sided_p_value']:.4f}."
        ),
        (
            f"- Clopper-Pearson 95% CI for the proportion of examples with marks correct and "
            f"raw incorrect ({cp['successes']}/{cp['trials']}; unit: example, assumes "
            f"independence): [{cp['interval'][0]:.4f}, {cp['interval'][1]:.4f}]."
        ),
        (
            f"- Two-sided exact McNemar test (unit: example, assumes independence): "
            f"p = {paired['mcnemar_exact_p_value']:.4g} over "
            f"{paired['discordant_pair_count']} discordant pairs."
        ),
        "",
        clustered["unit_of_analysis_note"],
        "",
        (
            "This describes the paired difference from adding the frozen marks overlay for this "
            "model, task family, prompt, and capture setup. It does not establish a mechanism or "
            "generalize to other GUI tasks or models."
        ),
        "",
        "## Proposal coverage and the marks condition",
        "",
        (
            f"Proposal coverage was {som['proposal_covered_count']}/"
            f"{som['proposal_total_count']} ({_percent(som['proposal_coverage'])}). This holds by "
            "the retention rule, not as a measured property of the proposer: an example is "
            "retained only if the target semantic ID occurs exactly once among the independently "
            f"collected candidates ([protocol, retention rule]({Path(PROTOCOL_PATH).name}"
            "#retention-validation-and-exclusions)). The marks condition therefore measures "
            f"selection among {_candidate_text(inputs['overlays'])}. Conditional selection "
            f"accuracy was {som['conditional_selection_correct_count']}/"
            f"{som['conditional_selection_total_count']} "
            f"({_percent(som['conditional_selection_accuracy'])})."
        ),
        "",
        "## Data integrity and exclusions",
        "",
        (
            f"Invalid output counts were raw={raw['invalid_output_count']} and "
            f"marks={marks['invalid_output_count']}; request-failure counts were "
            f"raw={raw['request_failure_count']} and marks={marks['request_failure_count']}. "
            "Invalid outputs and request failures stay in the denominator and score as "
            "incorrect. " + exclusion_sentence(results)
        ),
        "",
        "## Error review",
        "",
        error_review_paragraph(inputs["error_review"], decisions),
        "",
        "| Category | Error records |",
        "|---|---:|",
    ]
    for category, count in results["error_taxonomy"]["category_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines += ["", "Category meanings as recorded in the decisions file:", ""]
    for category, meaning in sorted(decisions["category_interpretation"].items()):
        lines.append(f"- {category}: {meaning}")
    lines += [
        "",
        "## Harness context",
        "",
        (
            f"The calls ran through the `{results['provider']}` harness. Mean input tokens per "
            f"call (`usage.input_tokens`) were {tokens['raw']['mean']:.2f} for raw "
            f"(n={tokens['raw']['count']}) and {tokens['marks']['mean']:.2f} for marks "
            f"(n={tokens['marks']['count']}), from the clustered supplement. The harness prompt "
            f"is not part of the recorded `{results['prompt_version']}`, and it is not recorded "
            "whether the harness prompt was identical across conditions."
        ),
        "",
        "## Limitations",
        "",
        "- One synthetic vendor-onboarding task family, one resolution, and one model were used.",
        (
            "- Target identity is aliased with screen state in the frozen dataset, so "
            "per-control descriptive rates do not identify a control-type effect."
        ),
        f"- `{results['model']}` is a moving provider alias rather than an immutable snapshot.",
        (
            f"- The raw-coordinate gap is specific to `{results['model']}` with "
            f"`{results['prompt_version']}`. With a revised prompt, `Claude Haiku 4.5` scored "
            f"{HISTORICAL_HAIKU_RAW_TEXT} raw on the same examples "
            f"([historical v3 report]({Path(HISTORICAL_V3_REPORT_PATH).name}), cited, not an "
            "input), so the marks-over-raw difference is specific to this model and prompt."
        ),
        (
            "- Both bootstrap intervals describe uncertainty over this frozen example set, not "
            "over other applications or model versions."
        ),
        "",
        "## Method",
        "",
        "Copied from the `method` block of the clustered supplement JSON:",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in clustered["method"].items()]
    lines += ["", "## Inputs and reproduction", "", "Generated offline from:", ""]
    lines += [f"- `{path}`" for path in INPUT_PATHS]
    lines += [
        "",
        "No model or network call is made by these commands:",
        "",
        "```bash",
        f".venv/bin/python {GENERATOR_PATH}",
        f".venv/bin/python {VERIFIER_PATH}",
        "```",
        "",
        f"Input digests are recorded in [`{PROVENANCE_PATH}`]({Path(PROVENANCE_PATH).name}).",
        "",
    ]
    return "\n".join(lines)


def build_provenance(root: Path, report_text: str) -> dict[str, Any]:
    """Return the v2 provenance record for ``report_text`` rendered from ``root``."""
    encoded = report_text.encode("utf-8")
    return {
        "schema_version": PROVENANCE_SCHEMA,
        "report_version": REPORT_VERSION,
        "report": {
            "path": REPORT_PATH,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
        },
        "generator": {"script": GENERATOR_PATH, "module": MODULE_PATH, "verifier": VERIFIER_PATH},
        "inputs": [{"path": path, "sha256": _sha256(root / path)} for path in INPUT_PATHS],
        "cited_documents": [{"path": path, "sha256": _sha256(root / path)} for path in CITED_PATHS],
        "retains": {
            "report": V1_REPORT_PATH,
            "provenance": V1_PROVENANCE_PATH,
            "note": "The v1 report and provenance are frozen and are not modified by v2.",
        },
        "applies_from": dict(APPLIES_FROM),
        "applies_from_note": APPLIES_FROM_NOTE,
        "model_calls": 0,
    }


def render_outputs(root: Path) -> tuple[str, str]:
    """Return (report markdown, provenance JSON text) regenerated from ``root``."""
    report = render_report_v2(load_inputs(root))
    provenance = json.dumps(build_provenance(root, report), indent=2, sort_keys=True) + "\n"
    return report, provenance


def verify_outputs(root: Path) -> dict[str, Any]:
    """Regenerate in memory and compare to the committed files; never writes."""
    report, provenance = render_outputs(root)
    drift = [
        path
        for path, expected in ((REPORT_PATH, report), (PROVENANCE_PATH, provenance))
        if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != expected
    ]
    if drift:
        raise GroundingReportV2Error("regenerated bytes differ for: " + ", ".join(drift))
    return {
        "schema_version": "pixelgym-grounding-report-v2-verification-v1",
        "status": "verified",
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "inputs_verified": list(INPUT_PATHS),
        "wrote_files": False,
    }
