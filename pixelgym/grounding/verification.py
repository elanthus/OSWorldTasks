"""Read-only verification for the canonical frozen grounding report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pixelgym.grounding.analysis import analyze_predictions
from pixelgym.grounding.report import render_report_markdown

VERIFICATION_SCHEMA_VERSION = "pixelgym-grounding-report-verification-v1"
PROVENANCE_SCHEMA_VERSION = "pixelgym-grounding-report-provenance-v1"
DEFAULT_PROVENANCE_PATH = Path("artifacts/grounding-report-provenance-v1.json")

CANONICAL_EXPERIMENT_ID = "pixelgym-grounding-v1-gpt-5.4-mini-2026-08-10"
CANONICAL_SOURCE_REVISION = "68e66b078c03c13e78ec224d7243c804d36f1a9c"
CANONICAL_REPORT_SHA256 = "a761eab5ebb3c397f9a752181b644f81622752346037c0a12b9e0cb1b3e39f87"
CANONICAL_RESULTS_SHA256 = "0d65ee6da145762904cc8ba57c204c2e39acd384af8b0796a1c752a79072850a"
HISTORICAL_EXPERIMENT_ID = "pixelgym-grounding-v3a-haiku-gemini-2026-08-21"
HISTORICAL_SOURCE_REVISION = "de412357fa081eb0a89059f3bb15fd8d80d80ffa"
HISTORICAL_ORIGINAL_SHA256 = "885d14d6247451b3b4ef7811311c0a9c5c92a4a4c661649e6b3ebcfc59e0db97"
HISTORICAL_PRESERVED_SHA256 = "62723fd7eb6337ca8012ce6c12856ea19936897ddc0fd2a3b4d31bef51ce75c4"
CANONICAL_IDENTITY: dict[str, str] = {
    "protocol_version": "pixelgym-grounding-v1",
    "prompt_version": "pixelgym-grounding-prompt-v1",
    "provider": "codex-cli",
    "model": "gpt-5.4-mini",
}
CANONICAL_HEADLINE: dict[str, Any] = {
    "bootstrap_95_ci_percentage_points": [35.0, 54.0],
    "condition_record_count": 200,
    "example_count": 100,
    "excluded_example_count": 0,
    "marks_correct_count": 100,
    "mcnemar_exact_p_value": 1.1368683772161603e-13,
    "paired_delta_percentage_points": 44.0,
    "raw_correct_count": 56,
}
CANONICAL_INPUT_PATHS = frozenset(
    {
        "artifacts/grounding-protocol.md",
        "artifacts/grounding-dataset.jsonl",
        "artifacts/grounding-overlays.jsonl",
        "artifacts/grounding-predictions.jsonl",
        "artifacts/grounding-error-review.jsonl",
        "artifacts/grounding-error-review-decisions.json",
    }
)


class GroundingVerificationError(ValueError):
    """Raised when canonical grounding evidence does not verify."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GroundingVerificationError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise GroundingVerificationError(f"{label} must be a string")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise GroundingVerificationError(f"{label} must be an integer")
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise GroundingVerificationError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GroundingVerificationError(f"invalid JSON in {label}: {path}: {exc}") from exc
    return _object(value, label)


def _load_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError as exc:
        raise GroundingVerificationError(f"missing {label}: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} line {line_number}"))
        except json.JSONDecodeError as exc:
            raise GroundingVerificationError(
                f"invalid JSON in {label}: {path}:{line_number}: {exc}"
            ) from exc
    return rows


def _load_json_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise GroundingVerificationError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GroundingVerificationError(f"invalid JSON in {label}: {path}: {exc}") from exc
    if not isinstance(value, list):
        raise GroundingVerificationError(f"{label} must be a JSON array")
    return [_object(item, f"{label} row {index}") for index, item in enumerate(value)]


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise GroundingVerificationError(
            f"{label} mismatch: expected {expected!r}, found {actual!r}"
        )


def _repository_path(repository_root: Path, relative_path: str, label: str) -> Path:
    candidate = repository_root / relative_path
    resolved_root = repository_root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise GroundingVerificationError(f"{label} escapes the repository: {relative_path!r}")
    return candidate


def _sha256(path: Path, cache: dict[Path, str]) -> str:
    resolved = path.resolve()
    if resolved not in cache:
        try:
            cache[resolved] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise GroundingVerificationError(f"missing evidence file: {path}") from exc
    return cache[resolved]


def _verify_recorded_file(
    *,
    repository_root: Path,
    record: dict[str, Any],
    label: str,
    digest_cache: dict[Path, str],
) -> Path:
    relative_path = _string(record.get("path"), f"{label}.path")
    expected_digest = _string(record.get("sha256"), f"{label}.sha256")
    path = _repository_path(repository_root, relative_path, label)
    actual_digest = _sha256(path, digest_cache)
    _expect(actual_digest, expected_digest, f"{label} SHA-256")
    if "size_bytes" in record:
        expected_size = _integer(record.get("size_bytes"), f"{label}.size_bytes")
        _expect(path.stat().st_size, expected_size, f"{label} size")
    return path


def _verify_identity(identity: dict[str, Any], label: str) -> None:
    _expect(identity, CANONICAL_IDENTITY, f"{label} experiment identity")


def _verify_report_text(
    report_text: str, identity: dict[str, Any], headline: dict[str, Any]
) -> None:
    identity_lines = (
        f"- Protocol: `{identity['protocol_version']}`",
        f"- Prompt: `{identity['prompt_version']}`",
        f"- Provider/model: `{identity['provider']}` / `{identity['model']}`",
    )
    if any(line not in report_text for line in identity_lines):
        raise GroundingVerificationError(
            "canonical report experiment identity does not match frozen provenance"
        )
    example_count = _integer(headline.get("example_count"), "headline.example_count")
    condition_count = _integer(
        headline.get("condition_record_count"), "headline.condition_record_count"
    )
    excluded_count = _integer(
        headline.get("excluded_example_count"), "headline.excluded_example_count"
    )
    sample_line = (
        f"- Sample: {example_count} examples, {condition_count} condition records, "
        f"{excluded_count} exclusions"
    )
    if sample_line not in report_text:
        raise GroundingVerificationError(
            "canonical report sample size does not match frozen provenance"
    )
    headline_fragments = (
        (
            f"**{headline['raw_correct_count']}/{example_count} "
            f"({100 * headline['raw_correct_count'] / example_count:.1f}%)**"
        ),
        (
            f"**{headline['marks_correct_count']}/{example_count} "
            f"({100 * headline['marks_correct_count'] / example_count:.1f}%)**"
        ),
        f"**{headline['paired_delta_percentage_points']:+.1f} percentage points**",
        (
            "**["
            f"{headline['bootstrap_95_ci_percentage_points'][0]:+.1f}, "
            f"{headline['bootstrap_95_ci_percentage_points'][1]:+.1f}]**"
        ),
    )
    if any(fragment not in report_text for fragment in headline_fragments):
        raise GroundingVerificationError(
            "canonical report headline statistics do not match frozen provenance"
        )


def _verify_referenced_image(
    *,
    repository_root: Path,
    row: dict[str, Any],
    path_field: str,
    digest_field: str,
    label: str,
    digest_cache: dict[Path, str],
) -> None:
    relative_path = _string(row.get(path_field), f"{label}.{path_field}")
    expected_digest = _string(row.get(digest_field), f"{label}.{digest_field}")
    path = _repository_path(repository_root, relative_path, label)
    _expect(_sha256(path, digest_cache), expected_digest, f"{label} image SHA-256")


def verify_grounding_report(
    repository_root: Path,
    *,
    provenance_path: Path = DEFAULT_PROVENANCE_PATH,
) -> dict[str, Any]:
    """Verify canonical identity, statistics, evidence hashes, and stored render bytes.

    The verifier is read-only. It recomputes the numerical result from the frozen JSONL
    records and checks stored PNG digests, but it does not regenerate report or image files.
    """
    repository_root = repository_root.resolve()
    manifest_path = (
        provenance_path
        if provenance_path.is_absolute()
        else _repository_path(repository_root, provenance_path.as_posix(), "provenance")
    )
    provenance = _load_json(manifest_path, "grounding report provenance")
    _expect(
        provenance.get("schema_version"),
        PROVENANCE_SCHEMA_VERSION,
        "provenance schema version",
    )
    canonical = _object(provenance.get("canonical"), "provenance.canonical")
    _expect(
        canonical.get("experiment_id"),
        CANONICAL_EXPERIMENT_ID,
        "canonical experiment ID",
    )
    identity = _object(canonical.get("identity"), "provenance.canonical.identity")
    _verify_identity(identity, "provenance")
    headline = _object(canonical.get("headline"), "provenance.canonical.headline")
    _expect(headline, CANONICAL_HEADLINE, "provenance canonical headline")

    digest_cache: dict[Path, str] = {}
    report_record = _object(canonical.get("report"), "provenance.canonical.report")
    _expect(report_record.get("sha256"), CANONICAL_REPORT_SHA256, "canonical report digest")
    _expect(
        report_record.get("source_revision"),
        CANONICAL_SOURCE_REVISION,
        "canonical report source revision",
    )
    report_path_value = _string(report_record.get("path"), "canonical report path")
    report_path = _repository_path(repository_root, report_path_value, "canonical report")
    try:
        report_text = report_path.read_text()
    except FileNotFoundError as exc:
        raise GroundingVerificationError(f"missing canonical report: {report_path}") from exc
    _verify_report_text(report_text, identity, headline)
    _verify_recorded_file(
        repository_root=repository_root,
        record=report_record,
        label="canonical report",
        digest_cache=digest_cache,
    )

    results_record = _object(canonical.get("results"), "provenance.canonical.results")
    _expect(
        results_record.get("sha256"), CANONICAL_RESULTS_SHA256, "canonical results digest"
    )
    _expect(
        results_record.get("source_revision"),
        CANONICAL_SOURCE_REVISION,
        "canonical results source revision",
    )
    results_path_value = _string(results_record.get("path"), "canonical results path")
    results_path = _repository_path(repository_root, results_path_value, "canonical results")
    results = _load_json(results_path, "canonical results")
    result_identity = {key: results.get(key) for key in CANONICAL_IDENTITY}
    _verify_identity(result_identity, "results")
    _expect(results.get("schema_version"), "pixelgym-grounding-results-v2", "results schema")

    collection = _object(results.get("collection"), "results.collection")
    for field in ("example_count", "condition_record_count", "excluded_example_count"):
        _expect(collection.get(field), headline.get(field), f"results sample {field}")
    conditions = _object(results.get("conditions"), "results.conditions")
    raw = _object(conditions.get("raw"), "results.conditions.raw")
    marks = _object(conditions.get("marks"), "results.conditions.marks")
    paired = _object(results.get("paired"), "results.paired")
    _expect(raw.get("correct_count"), headline.get("raw_correct_count"), "raw correct count")
    _expect(marks.get("correct_count"), headline.get("marks_correct_count"), "marks correct count")
    _expect(
        paired.get("delta_percentage_points"),
        headline.get("paired_delta_percentage_points"),
        "paired headline difference",
    )
    _expect(
        paired.get("bootstrap_95_ci_percentage_points"),
        headline.get("bootstrap_95_ci_percentage_points"),
        "paired bootstrap interval",
    )
    _expect(
        paired.get("mcnemar_exact_p_value"),
        headline.get("mcnemar_exact_p_value"),
        "paired McNemar p-value",
    )
    _verify_recorded_file(
        repository_root=repository_root,
        record=results_record,
        label="canonical results",
        digest_cache=digest_cache,
    )

    inputs = _object(results.get("inputs"), "results.inputs")
    _expect(set(inputs), CANONICAL_INPUT_PATHS, "canonical input inventory")
    for relative_path, expected_digest_value in sorted(inputs.items()):
        expected_digest = _string(expected_digest_value, f"results.inputs[{relative_path!r}]")
        input_path = _repository_path(repository_root, relative_path, "canonical input")
        _expect(
            _sha256(input_path, digest_cache),
            expected_digest,
            f"canonical input {relative_path} SHA-256",
        )

    dataset_path = repository_root / "artifacts/grounding-dataset.jsonl"
    overlays_path = repository_root / "artifacts/grounding-overlays.jsonl"
    predictions_path = repository_root / "artifacts/grounding-predictions.jsonl"
    reviews_path = repository_root / "artifacts/grounding-error-review.jsonl"
    examples = _load_jsonl(dataset_path, "grounding dataset")
    overlays = _load_jsonl(overlays_path, "grounding overlays")
    predictions = _load_jsonl(predictions_path, "grounding predictions")
    reviews = _load_jsonl(reviews_path, "grounding error reviews")
    _expect(len(examples), headline.get("example_count"), "dataset sample size")
    _expect(len(predictions), headline.get("condition_record_count"), "prediction record count")
    _expect(len(overlays), len(examples), "overlay record count")

    example_ids = [_string(row.get("example_id"), "dataset example_id") for row in examples]
    overlay_ids = [_string(row.get("example_id"), "overlay example_id") for row in overlays]
    _expect(len(set(example_ids)), len(example_ids), "dataset unique example count")
    _expect(len(set(overlay_ids)), len(overlay_ids), "overlay unique example count")
    _expect(set(overlay_ids), set(example_ids), "overlay example coverage")
    for index, row in enumerate(examples):
        _expect(
            row.get("protocol_version"),
            identity["protocol_version"],
            f"dataset row {index} protocol",
        )
        _verify_referenced_image(
            repository_root=repository_root,
            row=row,
            path_field="image_path",
            digest_field="image_sha256",
            label=f"dataset row {index}",
            digest_cache=digest_cache,
        )
    for index, row in enumerate(overlays):
        _expect(
            row.get("protocol_version"),
            identity["protocol_version"],
            f"overlay row {index} protocol",
        )
        for prefix in ("raw", "marked"):
            _verify_referenced_image(
                repository_root=repository_root,
                row=row,
                path_field=f"{prefix}_image_path",
                digest_field=f"{prefix}_image_sha256",
                label=f"overlay row {index} {prefix}",
                digest_cache=digest_cache,
            )
    for index, row in enumerate(predictions):
        _verify_referenced_image(
            repository_root=repository_root,
            row=row,
            path_field="image_path",
            digest_field="image_sha256",
            label=f"prediction row {index}",
            digest_cache=digest_cache,
        )

    analysis_config = _object(results.get("analysis_config"), "results.analysis_config")
    recomputed = analyze_predictions(
        examples=examples,
        predictions=predictions,
        error_reviews=reviews,
        bootstrap_samples=_integer(
            analysis_config.get("paired_bootstrap_samples"),
            "analysis_config.paired_bootstrap_samples",
        ),
        bootstrap_seed=_integer(
            analysis_config.get("paired_bootstrap_seed"),
            "analysis_config.paired_bootstrap_seed",
        ),
    )
    recomputed_identity = {key: recomputed.get(key) for key in CANONICAL_IDENTITY}
    _verify_identity(recomputed_identity, "recomputed evidence")
    recomputed_collection = _object(recomputed.get("collection"), "recomputed.collection")
    for field in ("example_count", "condition_record_count", "excluded_example_count"):
        _expect(recomputed_collection.get(field), headline.get(field), f"recomputed {field}")
    recomputed_conditions = _object(recomputed.get("conditions"), "recomputed.conditions")
    recomputed_raw = _object(recomputed_conditions.get("raw"), "recomputed.conditions.raw")
    recomputed_marks = _object(recomputed_conditions.get("marks"), "recomputed.conditions.marks")
    recomputed_paired = _object(recomputed.get("paired"), "recomputed.paired")
    _expect(
        recomputed_raw.get("correct_count"), headline.get("raw_correct_count"), "recomputed raw"
    )
    _expect(
        recomputed_marks.get("correct_count"),
        headline.get("marks_correct_count"),
        "recomputed marks",
    )
    _expect(
        recomputed_paired.get("delta_percentage_points"),
        headline.get("paired_delta_percentage_points"),
        "recomputed paired difference",
    )
    _expect(
        recomputed_paired.get("bootstrap_95_ci_percentage_points"),
        headline.get("bootstrap_95_ci_percentage_points"),
        "recomputed bootstrap interval",
    )
    _expect(
        recomputed_paired.get("mcnemar_exact_p_value"),
        headline.get("mcnemar_exact_p_value"),
        "recomputed McNemar p-value",
    )

    outputs = _object(results.get("outputs"), "results.outputs")
    gallery_rows: list[dict[str, Any]] | None = None
    for output_name, output_record_value in sorted(outputs.items()):
        output_record = _object(output_record_value, f"results.outputs.{output_name}")
        output_path = _verify_recorded_file(
            repository_root=repository_root,
            record=output_record,
            label=f"stored output {output_name}",
            digest_cache=digest_cache,
        )
        if output_name == "gallery_manifest":
            gallery_rows = _load_json_array(output_path, "gallery manifest")
            for index, item in enumerate(gallery_rows):
                image_path = _repository_path(
                    repository_root,
                    _string(item.get("image_path"), f"gallery manifest row {index}.image_path"),
                    f"gallery manifest row {index}",
                )
                if not image_path.is_file():
                    raise GroundingVerificationError(f"missing gallery image: {image_path}")
    if gallery_rows is None:
        raise GroundingVerificationError("results outputs omit the gallery manifest")
    _expect(
        report_text,
        render_report_markdown(results, gallery_rows),
        "canonical report content",
    )

    historical = _object(provenance.get("historical"), "provenance.historical")
    _expect(
        historical.get("experiment_id"),
        HISTORICAL_EXPERIMENT_ID,
        "historical experiment ID",
    )
    original_report = _object(
        historical.get("original_report"), "provenance.historical.original_report"
    )
    _expect(
        original_report.get("last_revision"),
        HISTORICAL_SOURCE_REVISION,
        "historical report source revision",
    )
    _expect(
        original_report.get("sha256"),
        HISTORICAL_ORIGINAL_SHA256,
        "historical original report digest",
    )
    preserved_report = _object(
        historical.get("preserved_report"), "provenance.historical.preserved_report"
    )
    _expect(
        preserved_report.get("path"),
        "artifacts/grounding-v3-haiku-gemini-report.md",
        "historical report path",
    )
    _expect(
        preserved_report.get("sha256"),
        HISTORICAL_PRESERVED_SHA256,
        "historical preserved report digest",
    )
    _verify_recorded_file(
        repository_root=repository_root,
        record=preserved_report,
        label="historical Haiku/Gemini report",
        digest_cache=digest_cache,
    )
    rendering = _object(
        provenance.get("rendering_reproducibility"), "provenance.rendering_reproducibility"
    )
    _expect(
        rendering.get("byte_level_regeneration_guarantee"),
        False,
        "rendered-image byte-level regeneration guarantee",
    )

    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "verified",
        "experiment_id": CANONICAL_EXPERIMENT_ID,
        "identity": CANONICAL_IDENTITY,
        "sample": {
            "example_count": headline["example_count"],
            "condition_record_count": headline["condition_record_count"],
            "excluded_example_count": headline["excluded_example_count"],
        },
        "headline": {
            "raw_correct_count": headline["raw_correct_count"],
            "marks_correct_count": headline["marks_correct_count"],
            "paired_delta_percentage_points": headline[
                "paired_delta_percentage_points"
            ],
            "bootstrap_95_ci_percentage_points": headline[
                "bootstrap_95_ci_percentage_points"
            ],
            "mcnemar_exact_p_value": headline["mcnemar_exact_p_value"],
        },
        "checked_file_count": len(digest_cache),
        "numerical_reproducibility": "recomputed from frozen JSONL evidence",
        "rendered_image_reproducibility": "stored bytes verified by recorded SHA-256 only",
        "wrote_files": False,
    }
