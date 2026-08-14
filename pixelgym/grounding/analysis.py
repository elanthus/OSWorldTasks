"""Offline paired analysis for the frozen grounding experiment."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from pixelgym.grounding.evaluation import PREDICTION_SCHEMA_VERSION, PROMPT_VERSION
from pixelgym.grounding.schema import (
    PROTOCOL_VERSION,
    TARGET_AREA_MEDIUM_BELOW,
    TARGET_AREA_SMALL_BELOW,
    target_area_slice,
)

ANALYSIS_SCHEMA_VERSION = "pixelgym-grounding-results-v2"
ERROR_REVIEW_SCHEMA_VERSION = "pixelgym-grounding-error-review-v2"
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20_260_809

ERROR_CATEGORIES = (
    "wrong semantic element",
    "correct region but point just outside the box",
    "coordinate scaling error",
    "small target",
    "crowded or overlapping controls",
    "ambiguous instruction",
    "mark omitted or illegible",
    "correct proposal, wrong mark selection",
    "invalid response format",
)
REVIEW_STATUSES = ("pending_visual_review", "manual_visual_review")
ERROR_REVIEW_DECISIONS_SCHEMA_VERSION = "pixelgym-grounding-error-review-decisions-v1"


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap_interval(
    paired_differences: list[int],
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Return the percentile 95% CI for a paired mean difference."""
    if not paired_differences:
        raise ValueError("paired bootstrap requires at least one example")
    if samples <= 0:
        raise ValueError("bootstrap samples must be positive")
    rng = random.Random(seed)
    count = len(paired_differences)
    estimates = [
        sum(paired_differences[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    ]
    estimates.sort()
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def mcnemar_exact(raw_only_correct: int, marks_only_correct: int) -> float:
    """Two-sided exact McNemar p-value, conditional on discordant pairs."""
    if raw_only_correct < 0 or marks_only_correct < 0:
        raise ValueError("discordant counts must be nonnegative")
    discordant = raw_only_correct + marks_only_correct
    if discordant == 0:
        return 1.0
    smaller = min(raw_only_correct, marks_only_correct)
    lower_tail = sum(math.comb(discordant, k) for k in range(smaller + 1)) / (2**discordant)
    return min(1.0, 2 * lower_tail)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _condition_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(record["correct"] is True for record in records)
    invalid = sum(record["parse_status"] == "invalid" for record in records)
    request_failures = sum(record["parse_status"] == "request_failure" for record in records)
    distances = [
        float(record["normalized_center_distance"])
        for record in records
        if record["normalized_center_distance"] is not None
    ]
    distance_summary: dict[str, Any] = {
        "record_count": len(records),
        "measured_count": len(distances),
        "missing_count": len(records) - len(distances),
        "mean": statistics.fmean(distances) if distances else None,
        "median": statistics.median(distances) if distances else None,
        "p90": _percentile(sorted(distances), 0.9) if distances else None,
    }
    return {
        "record_count": len(records),
        "correct_count": correct,
        "accuracy": _rate(correct, len(records)),
        "invalid_output_count": invalid,
        "invalid_output_rate": _rate(invalid, len(records)),
        "request_failure_count": request_failures,
        "request_failure_rate": _rate(request_failures, len(records)),
        "parse_status_counts": dict(
            sorted(Counter(row["parse_status"] for row in records).items())
        ),
        "normalized_center_distance": distance_summary,
    }


def _slice_summary(pairs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(pairs)
    raw_correct = sum(row["raw"]["correct"] for row in rows)
    marks_correct = sum(row["marks"]["correct"] for row in rows)
    return {
        "example_count": len(rows),
        "raw_correct_count": raw_correct,
        "raw_accuracy": _rate(raw_correct, len(rows)),
        "marks_correct_count": marks_correct,
        "marks_accuracy": _rate(marks_correct, len(rows)),
        "paired_delta_percentage_points": 100 * (marks_correct - raw_correct) / len(rows),
    }


def _latency_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["latency_ms"]) for row in records if row.get("latency_ms") is not None]
    return {
        "record_count": len(records),
        "observed_count": len(values),
        "missing_count": len(records) - len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "minimum_ms": min(values) if values else None,
        "maximum_ms": max(values) if values else None,
        "total_ms": sum(values),
    }


def _usage_totals(records: list[dict[str, Any]]) -> dict[str, int | float]:
    totals: defaultdict[str, int | float] = defaultdict(int)
    for record in records:
        usage = record.get("usage") or {}
        for key, value in usage.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                totals[key] += value
    return dict(sorted(totals.items()))


def _validate_and_pair(
    examples: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not examples:
        raise ValueError("grounding dataset is empty")
    example_by_id = {row["example_id"]: row for row in examples}
    if len(example_by_id) != len(examples):
        raise ValueError("grounding dataset contains duplicate example IDs")
    grouped: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in predictions:
        if record.get("schema_version") != PREDICTION_SCHEMA_VERSION:
            raise ValueError("prediction schema version does not match")
        if record.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("prediction protocol version does not match")
        if record.get("prompt_version") != PROMPT_VERSION:
            raise ValueError("prediction prompt version does not match")
        example_id = record.get("example_id")
        if example_id not in example_by_id:
            raise ValueError(f"prediction references unknown example {example_id!r}")
        condition = record.get("condition")
        if condition not in {"raw", "marks"}:
            raise ValueError(f"unknown prediction condition {condition!r}")
        if condition in grouped[example_id]:
            raise ValueError(f"duplicate prediction for {example_id}/{condition}")
        if type(record.get("correct")) is not bool:
            raise ValueError("prediction correct field must be boolean")
        grouped[example_id][condition] = record
    if set(grouped) != set(example_by_id):
        missing = sorted(set(example_by_id) - set(grouped))
        raise ValueError(f"predictions do not cover the frozen dataset; missing={missing[:5]}")
    if any(set(pair) != {"raw", "marks"} for pair in grouped.values()):
        raise ValueError("every example must have exactly one raw and one marks prediction")

    pairs = []
    for example in examples:
        pair = grouped[example["example_id"]]
        pairs.append(
            {
                "example_id": example["example_id"],
                "target_id": example["target_id"],
                "target": example["target"],
                "element_type": example["element_type"],
                "target_size": target_area_slice(example),
                "screen_state": example["screen_state"],
                "task_seed": example["task_seed"],
                "bbox": example["bbox"],
                "raw": pair["raw"],
                "marks": pair["marks"],
            }
        )
    return pairs


def _suggest_error_category(
    *, example: dict[str, Any], record: dict[str, Any], condition: str
) -> tuple[str, str, str]:
    bbox = example["bbox"]
    observation = (
        f"parse_status={record['parse_status']}; point={record['point']}; "
        f"target_bbox={bbox}; correct={record['correct']}"
    )
    if record["parse_status"] != "parsed":
        return "invalid response format", observation, "Suggested from parser status."
    if condition == "marks":
        if record["target_proposed"] is False:
            return (
                "mark omitted or illegible",
                observation + "; target_proposed=False",
                "Suggested because the candidate generator omitted the target.",
            )
        return (
            "correct proposal, wrong mark selection",
            observation + f"; selected_mark_id={record['mark_id']}",
            "Suggested because the target was proposed but the selected mark was incorrect.",
        )
    point = record["point"]
    if point is not None:
        x, y = point
        x0, y0, x1, y1 = bbox
        outside_x = max(x0 - x, 0, x - (x1 - 1))
        outside_y = max(y0 - y, 0, y - (y1 - 1))
        if math.hypot(outside_x, outside_y) <= max(4, min(x1 - x0, y1 - y0) / 2):
            return (
                "correct region but point just outside the box",
                observation,
                "Suggested from geometric proximity; visual review is still required.",
            )
    if target_area_slice(example) == "small":
        return "small target", observation, "Suggested from the frozen target-area threshold."
    if example["element_type"] == "radio":
        return (
            "crowded or overlapping controls",
            observation,
            "Suggested because the target is one of adjacent radio controls.",
        )
    return (
        "wrong semantic element",
        observation,
        "Fallback suggestion; inspect the annotated pair before accepting it.",
    )


def build_error_review_template(
    examples: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Create an explicitly unreviewed taxonomy template for every incorrect record."""
    pairs = _validate_and_pair(examples, predictions)
    example_by_id = {row["example_id"]: row for row in examples}
    reviews = []
    for pair in pairs:
        example = example_by_id[pair["example_id"]]
        for condition in ("raw", "marks"):
            record = pair[condition]
            if record["correct"]:
                continue
            category, observation, inference = _suggest_error_category(
                example=example, record=record, condition=condition
            )
            reviews.append(
                {
                    "schema_version": ERROR_REVIEW_SCHEMA_VERSION,
                    "example_id": pair["example_id"],
                    "condition": condition,
                    "categories": [category],
                    "review_status": "pending_visual_review",
                    "observation": observation,
                    "inference": inference,
                    "review_image_path": (
                        f"artifacts/grounding/gallery/errors/{pair['example_id']}-{condition}.png"
                    ),
                }
            )
    return reviews


def apply_manual_error_review_decisions(
    reviews: list[dict[str, Any]], decisions: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply checked visual-review decisions to the generated error template."""
    if decisions.get("schema_version") != ERROR_REVIEW_DECISIONS_SCHEMA_VERSION:
        raise ValueError("error review decisions schema version does not match")
    if decisions.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("error review decisions protocol version does not match")
    assignments = decisions.get("category_assignments")
    interpretations = decisions.get("category_interpretation")
    if not isinstance(assignments, dict) or not isinstance(interpretations, dict):
        raise TypeError("error review decisions must contain assignments and interpretations")
    if set(assignments) - set(ERROR_CATEGORIES):
        raise ValueError("error review decisions contain an unknown category")
    if set(assignments) != set(interpretations):
        raise ValueError("every assigned category must have an interpretation")
    expected_keys = {f"{row['example_id']}/{row['condition']}" for row in reviews}
    assigned_categories: defaultdict[str, list[str]] = defaultdict(list)
    for category in ERROR_CATEGORIES:
        keys = assignments.get(category, [])
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError("category assignments must be string lists")
        if len(keys) != len(set(keys)):
            raise ValueError(f"category {category!r} contains duplicate review keys")
        for key in keys:
            if key not in expected_keys:
                raise ValueError(f"category assignment references unknown error {key!r}")
            assigned_categories[key].append(category)
    if set(assigned_categories) != expected_keys:
        missing = sorted(expected_keys - set(assigned_categories))
        raise ValueError(f"manual decisions do not cover every error; missing={missing}")

    finalized = []
    for review in reviews:
        key = f"{review['example_id']}/{review['condition']}"
        categories = assigned_categories[key]
        finalized.append(
            {
                **review,
                "categories": categories,
                "review_status": "manual_visual_review",
                "inference": " ".join(interpretations[category] for category in categories),
            }
        )
    return finalized


def _validate_error_reviews(
    pairs: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {
        (pair["example_id"], condition)
        for pair in pairs
        for condition in ("raw", "marks")
        if not pair[condition]["correct"]
    }
    actual: set[tuple[str, str]] = set()
    required = {
        "schema_version",
        "example_id",
        "condition",
        "categories",
        "review_status",
        "observation",
        "inference",
        "review_image_path",
    }
    for review in reviews:
        if set(review) != required:
            raise ValueError("error review fields do not match the frozen schema")
        if review["schema_version"] != ERROR_REVIEW_SCHEMA_VERSION:
            raise ValueError("error review schema version does not match")
        key = (review["example_id"], review["condition"])
        if key in actual:
            raise ValueError(f"duplicate error review for {key}")
        actual.add(key)
        categories = review["categories"]
        if (
            not isinstance(categories, list)
            or not categories
            or any(not isinstance(category, str) for category in categories)
            or len(categories) != len(set(categories))
        ):
            raise ValueError("error review categories must be a nonempty unique string list")
        unknown_categories = sorted(set(categories) - set(ERROR_CATEGORIES))
        if unknown_categories:
            raise ValueError(f"unknown error categories {unknown_categories!r}")
        if review["review_status"] not in REVIEW_STATUSES:
            raise ValueError(f"unknown review status {review['review_status']!r}")
        textual_fields = required - {"schema_version", "categories"}
        if not all(isinstance(review[field], str) for field in textual_fields):
            raise ValueError("error review textual fields must be strings")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"error reviews do not match incorrect records; missing={missing}, extra={extra}"
        )
    return reviews


def analyze_predictions(
    *,
    examples: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    error_reviews: list[dict[str, Any]],
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Analyze stored predictions without making provider calls."""
    pairs = _validate_and_pair(examples, predictions)
    reviews = _validate_error_reviews(pairs, error_reviews)
    raw_records = [pair["raw"] for pair in pairs]
    marks_records = [pair["marks"] for pair in pairs]
    differences = [int(pair["marks"]["correct"]) - int(pair["raw"]["correct"]) for pair in pairs]
    ci_low, ci_high = paired_bootstrap_interval(
        differences, samples=bootstrap_samples, seed=bootstrap_seed
    )
    raw_only = sum(pair["raw"]["correct"] and not pair["marks"]["correct"] for pair in pairs)
    marks_only = sum(pair["marks"]["correct"] and not pair["raw"]["correct"] for pair in pairs)
    both_correct = sum(pair["raw"]["correct"] and pair["marks"]["correct"] for pair in pairs)
    both_incorrect = sum(
        not pair["raw"]["correct"] and not pair["marks"]["correct"] for pair in pairs
    )
    by_element: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_size: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        by_element[pair["element_type"]].append(pair)
        by_size[pair["target_size"]].append(pair)
    proposal_count = sum(record["target_proposed"] is True for record in marks_records)
    conditional_correct = sum(
        record["target_proposed"] is True and record["correct"] for record in marks_records
    )
    timestamps = [record.get("timestamp_utc") for record in predictions]
    observed_timestamps = [value for value in timestamps if isinstance(value, str) and value]
    providers = sorted({record["provider"] for record in predictions})
    models = sorted({record["model"] for record in predictions})
    parameter_encodings = {repr(sorted(record["parameters"].items())) for record in predictions}
    if len(providers) != 1 or len(models) != 1 or len(parameter_encodings) != 1:
        raise ValueError(
            "provider, model, and parameters must be constant across the paired experiment"
        )

    condition_metrics = {
        "raw": _condition_summary(raw_records),
        "marks": _condition_summary(marks_records),
    }
    delta_percentage_points = (
        100
        * (condition_metrics["marks"]["correct_count"] - condition_metrics["raw"]["correct_count"])
        / len(pairs)
    )
    per_example = []
    for pair in pairs:
        per_example.append(
            {
                "example_id": pair["example_id"],
                "target_id": pair["target_id"],
                "element_type": pair["element_type"],
                "target_size": pair["target_size"],
                "screen_state": pair["screen_state"],
                "task_seed": pair["task_seed"],
                "bbox": pair["bbox"],
                "raw": {
                    "correct": pair["raw"]["correct"],
                    "parse_status": pair["raw"]["parse_status"],
                    "point": pair["raw"]["point"],
                    "normalized_center_distance": pair["raw"]["normalized_center_distance"],
                    "cache_key": pair["raw"]["cache_key"],
                },
                "marks": {
                    "correct": pair["marks"]["correct"],
                    "parse_status": pair["marks"]["parse_status"],
                    "point": pair["marks"]["point"],
                    "mark_id": pair["marks"]["mark_id"],
                    "target_proposed": pair["marks"]["target_proposed"],
                    "normalized_center_distance": pair["marks"]["normalized_center_distance"],
                    "cache_key": pair["marks"]["cache_key"],
                },
                "paired_difference": int(pair["marks"]["correct"]) - int(pair["raw"]["correct"]),
            }
        )

    review_status_counts = Counter(review["review_status"] for review in reviews)
    category_counts = Counter(category for review in reviews for category in review["categories"])
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "analysis_config": {
            "paired_bootstrap_samples": bootstrap_samples,
            "paired_bootstrap_seed": bootstrap_seed,
            "confidence_level": 0.95,
            "bootstrap_interval": "percentile",
            "mcnemar_test": "two-sided exact binomial",
            "distance_normalization": "screenshot diagonal",
            "invalid_outputs_scored_incorrect": True,
            "request_failures_scored_incorrect": True,
            "target_size_thresholds_screen_area": {
                "small_below": TARGET_AREA_SMALL_BELOW,
                "medium_below": TARGET_AREA_MEDIUM_BELOW,
            },
        },
        "provider": providers[0],
        "model": models[0],
        "parameters": predictions[0]["parameters"],
        "collection": {
            "first_timestamp_utc": min(observed_timestamps) if observed_timestamps else None,
            "last_timestamp_utc": max(observed_timestamps) if observed_timestamps else None,
            "timestamp_observed_count": len(observed_timestamps),
            "timestamp_missing_count": len(predictions) - len(observed_timestamps),
            "example_count": len(pairs),
            "condition_record_count": len(predictions),
            "excluded_example_count": 0,
        },
        "conditions": condition_metrics,
        "paired": {
            "delta_percentage_points": delta_percentage_points,
            "bootstrap_95_ci_percentage_points": [100 * ci_low, 100 * ci_high],
            "both_correct_count": both_correct,
            "raw_only_correct_count": raw_only,
            "marks_only_correct_count": marks_only,
            "both_incorrect_count": both_incorrect,
            "discordant_pair_count": raw_only + marks_only,
            "mcnemar_exact_p_value": mcnemar_exact(raw_only, marks_only),
        },
        "set_of_marks": {
            "proposal_covered_count": proposal_count,
            "proposal_total_count": len(marks_records),
            "proposal_coverage": _rate(proposal_count, len(marks_records)),
            "conditional_selection_correct_count": conditional_correct,
            "conditional_selection_total_count": proposal_count,
            "conditional_selection_accuracy": _rate(conditional_correct, proposal_count),
        },
        "slices": {
            "element_type": {
                key: _slice_summary(value) for key, value in sorted(by_element.items())
            },
            "target_size": {key: _slice_summary(value) for key, value in sorted(by_size.items())},
        },
        "latency": {
            "all": _latency_summary(predictions),
            "raw": _latency_summary(raw_records),
            "marks": _latency_summary(marks_records),
        },
        "usage_totals": _usage_totals(predictions),
        "error_taxonomy": {
            "error_record_count": len(reviews),
            "category_counts": dict(sorted(category_counts.items())),
            "review_status_counts": dict(sorted(review_status_counts.items())),
            "all_manually_reviewed": review_status_counts.get("pending_visual_review", 0) == 0,
            "records": reviews,
        },
        "per_example": per_example,
    }
