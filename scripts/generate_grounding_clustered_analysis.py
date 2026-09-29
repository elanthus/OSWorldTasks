#!/usr/bin/env python3
"""Generate the target-clustered supplement to the frozen v1 grounding analysis.

Reads only ``artifacts/grounding-results.json`` and ``artifacts/grounding-predictions.jsonl``
and writes ``artifacts/grounding-clustered-analysis-v1.json`` plus a Markdown rendering of it.
The frozen report and its inputs are not modified. Standard library only; randomness comes from
``random.Random(20260809)``, the same generator and seed as the frozen v1 interval.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pixelgym-grounding-clustered-analysis-v1"
RESULTS_PATH = "artifacts/grounding-results.json"
PREDICTIONS_PATH = "artifacts/grounding-predictions.jsonl"
JSON_OUTPUT = "artifacts/grounding-clustered-analysis-v1.json"
MD_OUTPUT = "artifacts/grounding-clustered-analysis-v1.md"
SEED = 20260809
SAMPLES = 10_000
CONFIDENCE = 0.95
EXPECTED_TARGETS = 10
EXPECTED_PER_TARGET = 10


class ClusteredAnalysisError(ValueError):
    """Raised when the frozen inputs do not have the expected structure."""


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolation percentile, identical to pixelgym.grounding.analysis._percentile."""
    if not sorted_values:
        raise ClusteredAnalysisError("cannot calculate a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def load_rows(root: Path) -> list[dict[str, Any]]:
    """Return per-example rows cross-checked against the stored predictions."""
    results = json.loads((root / RESULTS_PATH).read_text(encoding="utf-8"))
    rows = results.get("per_example")
    if not isinstance(rows, list) or not rows:
        raise ClusteredAnalysisError("results file has no per_example rows")
    predictions: dict[tuple[str, str], bool] = {}
    input_tokens: dict[tuple[str, str], int] = {}
    for line in (root / PREDICTIONS_PATH).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        key = (record["example_id"], record["condition"])
        if key in predictions:
            raise ClusteredAnalysisError(f"duplicate prediction {key}")
        predictions[key] = record["correct"] is True
        tokens = (record.get("usage") or {}).get("input_tokens")
        if not isinstance(tokens, int):
            raise ClusteredAnalysisError(f"prediction {key} lacks integer usage.input_tokens")
        input_tokens[key] = tokens
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        example_id, target_id = row.get("example_id"), row.get("target_id")
        if not isinstance(example_id, str) or not isinstance(target_id, str):
            raise ClusteredAnalysisError("per_example row lacks example_id or target_id")
        if example_id in seen:
            raise ClusteredAnalysisError(f"duplicate example {example_id}")
        seen.add(example_id)
        raw, marks = row["raw"]["correct"] is True, row["marks"]["correct"] is True
        for condition, value in (("raw", raw), ("marks", marks)):
            if predictions.get((example_id, condition)) != value:
                raise ClusteredAnalysisError(
                    f"results and predictions disagree for {example_id}/{condition}"
                )
        if row.get("paired_difference") != int(marks) - int(raw):
            raise ClusteredAnalysisError(f"paired_difference mismatch for {example_id}")
        parsed.append(
            {
                "example_id": example_id,
                "target_id": target_id,
                "raw": raw,
                "marks": marks,
                "raw_input_tokens": input_tokens[(example_id, "raw")],
                "marks_input_tokens": input_tokens[(example_id, "marks")],
            }
        )
    if len(predictions) != 2 * len(parsed):
        raise ClusteredAnalysisError("predictions contain records absent from results")
    counts = Counter(row["target_id"] for row in parsed)
    if len(counts) != EXPECTED_TARGETS or set(counts.values()) != {EXPECTED_PER_TARGET}:
        raise ClusteredAnalysisError(
            f"expected {EXPECTED_TARGETS} targets x {EXPECTED_PER_TARGET} examples, "
            f"got {dict(counts)}"
        )
    return parsed


def example_bootstrap(differences: list[int], samples: int, seed: int) -> tuple[float, float]:
    """Example-level percentile bootstrap; same draw order as the frozen v1 analysis."""
    if not differences or samples <= 0:
        raise ClusteredAnalysisError("bootstrap requires data and a positive sample count")
    rng = random.Random(seed)
    n = len(differences)
    estimates = sorted(
        sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples)
    )
    alpha = (1 - CONFIDENCE) / 2
    return percentile(estimates, alpha), percentile(estimates, 1 - alpha)


def cluster_bootstrap(clusters: list[list[int]], samples: int, seed: int) -> tuple[float, float]:
    """Target-clustered percentile bootstrap: resample whole targets with replacement.

    Draw order: for each resample, draw len(clusters) indices with ``rng.randrange`` in sequence;
    the estimate is the pooled mean paired difference over all examples in the drawn clusters.
    """
    if not clusters or samples <= 0:
        raise ClusteredAnalysisError("bootstrap requires data and a positive sample count")
    rng = random.Random(seed)
    k = len(clusters)
    estimates = []
    for _ in range(samples):
        drawn = [clusters[rng.randrange(k)] for _ in range(k)]
        estimates.append(sum(sum(c) for c in drawn) / sum(len(c) for c in drawn))
    estimates.sort()
    alpha = (1 - CONFIDENCE) / 2
    return percentile(estimates, alpha), percentile(estimates, 1 - alpha)


def sign_test_two_sided(positive: int, negative: int) -> float:
    """Exact two-sided binomial sign test with p=0.5 (ties excluded)."""
    n = positive + negative
    if n == 0:
        return 1.0
    tail = min(positive, negative)
    p: float = 2 * sum(math.comb(n, i) for i in range(tail + 1)) / 2**n
    return min(1.0, p)


def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _bisect(predicate_high: Callable[[float], bool]) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if predicate_high(mid):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(successes: int, n: int, confidence: float = CONFIDENCE) -> tuple[float, float]:
    """Exact Clopper-Pearson interval by bisection on the binomial CDF."""
    if n <= 0 or not 0 <= successes <= n:
        raise ClusteredAnalysisError("invalid binomial counts")
    alpha = (1 - confidence) / 2
    lower = (
        0.0 if successes == 0 else _bisect(lambda p: 1 - _binom_cdf(successes - 1, n, p) >= alpha)
    )
    upper = 1.0 if successes == n else _bisect(lambda p: _binom_cdf(successes, n, p) <= alpha)
    return lower, upper


def analyze(rows: list[dict[str, Any]], samples: int = SAMPLES, seed: int = SEED) -> dict[str, Any]:
    differences = [int(r["marks"]) - int(r["raw"]) for r in rows]
    order: list[str] = []
    for r in rows:
        if r["target_id"] not in order:
            order.append(r["target_id"])
    per_target = []
    clusters = []
    for target in order:
        members = [r for r in rows if r["target_id"] == target]
        diffs = [int(r["marks"]) - int(r["raw"]) for r in members]
        clusters.append(diffs)
        per_target.append(
            {
                "target_id": target,
                "example_count": len(members),
                "raw_correct": sum(r["raw"] for r in members),
                "marks_correct": sum(r["marks"] for r in members),
                "paired_difference_sum": sum(diffs),
            }
        )
    pos = sum(t["paired_difference_sum"] > 0 for t in per_target)
    neg = sum(t["paired_difference_sum"] < 0 for t in per_target)
    ex_lo, ex_hi = example_bootstrap(differences, samples, seed)
    cl_lo, cl_hi = cluster_bootstrap(clusters, samples, seed)
    improved = sum(d == 1 for d in differences)
    cp_lo, cp_hi = clopper_pearson(improved, len(differences))
    return {
        "schema_version": SCHEMA_VERSION,
        "inputs": [RESULTS_PATH, PREDICTIONS_PATH],
        "method": {
            "rng": f"Python random.Random({seed}); rng.randrange draws",
            "bootstrap_samples": samples,
            "confidence_level": CONFIDENCE,
            "percentile_convention": (
                "linear interpolation at position (n-1)*q, identical to "
                "pixelgym.grounding.analysis._percentile used for the frozen v1 interval"
            ),
            "example_level_draw_order": (
                "per resample, n sequential rng.randrange(n) draws over per_example order; "
                "same as pixelgym.grounding.analysis.paired_bootstrap_interval"
            ),
            "cluster_level_draw_order": (
                "fresh random.Random(seed); per resample, k sequential rng.randrange(k) draws "
                "over targets in first-appearance order; pooled mean of drawn examples"
            ),
            "sign_test": (
                "exact two-sided binomial, p=0.5, over targets; targets with zero net "
                "difference excluded"
            ),
            "clopper_pearson": (
                "exact binomial interval for the proportion of examples with marks correct "
                "and raw incorrect"
            ),
        },
        "counts": {
            "example_count": len(rows),
            "target_count": len(order),
            "raw_correct": sum(r["raw"] for r in rows),
            "marks_correct": sum(r["marks"] for r in rows),
            "marks_only_correct": improved,
            "raw_only_correct": sum(d == -1 for d in differences),
        },
        "per_target": per_target,
        "delta_percentage_points": 100 * sum(differences) / len(differences),
        "example_level_bootstrap_95_ci_percentage_points": [
            round(100 * ex_lo, 9),
            round(100 * ex_hi, 9),
        ],
        "target_clustered_bootstrap_95_ci_percentage_points": [
            round(100 * cl_lo, 9),
            round(100 * cl_hi, 9),
        ],
        "harness_input_tokens": harness_input_tokens(rows),
        "target_sign_test": {
            "targets_marks_better": pos,
            "targets_raw_better": neg,
            "targets_tied": len(order) - pos - neg,
            "two_sided_p_value": sign_test_two_sided(pos, neg),
        },
        "clopper_pearson_95_ci_marks_only_proportion": {
            "successes": improved,
            "trials": len(differences),
            "interval": [cp_lo, cp_hi],
        },
        "unit_of_analysis_note": (
            "The examples are target controls each repeated across seeds. Raw-condition success "
            "is strongly shared within a target, so examples of the same target are not "
            "independent. The example-level bootstrap treats every example as independent and "
            "therefore overstates precision; the target-clustered bootstrap resamples whole "
            "targets and reflects how many independent controls were actually tested. Both "
            "intervals are reported, each with its unit of analysis named; neither replaces the "
            "frozen v1 report."
        ),
    }


def harness_input_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-condition usage.input_tokens summary from the stored predictions."""
    block: dict[str, Any] = {
        "source": f"{PREDICTIONS_PATH} usage.input_tokens (Codex CLI harness, per call)"
    }
    for condition in ("raw", "marks"):
        values = [r[f"{condition}_input_tokens"] for r in rows]
        block[condition] = {
            "count": len(values),
            "mean": round(sum(values) / len(values), 9),
            "min": min(values),
            "max": max(values),
        }
    return block


def _pp(value: float) -> str:
    return f"{value:+.1f}"


def render_markdown(result: dict[str, Any]) -> str:
    """Render the Markdown supplement using only values present in ``result``."""
    c = result["counts"]
    ex = result["example_level_bootstrap_95_ci_percentage_points"]
    cl = result["target_clustered_bootstrap_95_ci_percentage_points"]
    st = result["target_sign_test"]
    cp = result["clopper_pearson_95_ci_marks_only_proportion"]
    tok = result["harness_input_tokens"]
    per_target_size = result["per_target"][0]["example_count"]
    lines = [
        "# Grounding v1 clustered-analysis supplement",
        "",
        (
            f"Generated by `scripts/generate_grounding_clustered_analysis.py` from "
            f"`{result['inputs'][0]}` and `{result['inputs'][1]}`. Structured source: "
            "`artifacts/grounding-clustered-analysis-v1.json`. This supplements, and does not "
            "replace, the frozen `artifacts/grounding-report.md`."
        ),
        "",
        "## Counts",
        "",
        (
            f"{c['example_count']} paired examples ({c['target_count']} target controls x "
            f"{per_target_size} seeds). Raw {c['raw_correct']}/{c['example_count']}, marks "
            f"{c['marks_correct']}/{c['example_count']}, paired difference "
            f"{_pp(result['delta_percentage_points'])} pp."
        ),
        "",
        "| Target | Examples | Raw correct | Marks correct | Net marks-minus-raw |",
        "|---|---:|---:|---:|---:|",
    ]
    for t in result["per_target"]:
        lines.append(
            f"| `{t['target_id']}` | {t['example_count']} | {t['raw_correct']} | "
            f"{t['marks_correct']} | {t['paired_difference_sum']} |"
        )
    lines += [
        "",
        "## Intervals and tests",
        "",
        (
            f"- Example-level percentile bootstrap (unit: example, n={c['example_count']}): "
            f"95% CI [{_pp(ex[0])}, {_pp(ex[1])}] pp."
        ),
        (
            f"- Target-clustered percentile bootstrap (unit: target control, "
            f"n={c['target_count']}): 95% CI [{_pp(cl[0])}, {_pp(cl[1])}] pp."
        ),
        (
            f"- Target-level exact sign test: {st['targets_marks_better']} targets favour marks, "
            f"{st['targets_raw_better']} favour raw, {st['targets_tied']} tied; two-sided "
            f"p = {st['two_sided_p_value']:.4f}."
        ),
        (
            f"- Clopper-Pearson 95% CI for {cp['successes']}/{cp['trials']} marks-only-correct "
            f"examples: [{cp['interval'][0]:.4f}, {cp['interval'][1]:.4f}] (unit: example; "
            "assumes independence)."
        ),
        "",
        (
            f"- Harness input tokens per call (usage.input_tokens): raw mean "
            f"{tok['raw']['mean']:.2f} (n={tok['raw']['count']}, min {tok['raw']['min']}, "
            f"max {tok['raw']['max']}); marks mean {tok['marks']['mean']:.2f} "
            f"(n={tok['marks']['count']}, min {tok['marks']['min']}, max {tok['marks']['max']})."
        ),
        "",
        "## Why the example-level interval overstates precision",
        "",
        result["unit_of_analysis_note"],
        "",
        "## Method",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in result["method"].items()]
    return "\n".join(lines) + "\n"


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the target-clustered grounding v1 supplement."
    )
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    result = analyze(load_rows(args.repository_root))
    out = args.output_dir or args.repository_root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    (out / Path(JSON_OUTPUT).name).write_text(serialize(result), encoding="utf-8")
    (out / Path(MD_OUTPUT).name).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
