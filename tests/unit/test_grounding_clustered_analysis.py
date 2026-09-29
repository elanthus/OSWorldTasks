"""Tests for scripts/generate_grounding_clustered_analysis.py (no subprocess, no network)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "generate_grounding_clustered_analysis",
    ROOT / "scripts" / "generate_grounding_clustered_analysis.py",
)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mod
_SPEC.loader.exec_module(mod)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, Any]]:
    return mod.load_rows(ROOT)


def _copy_inputs(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir()
    for rel in (mod.RESULTS_PATH, mod.PREDICTIONS_PATH):
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    return tmp_path


def test_full_run_reproduces_frozen_interval_and_clustered_is_wider(
    rows: list[dict[str, Any]],
) -> None:
    result = mod.analyze(rows)
    frozen = json.loads((ROOT / mod.RESULTS_PATH).read_text())["paired"]
    example = result["example_level_bootstrap_95_ci_percentage_points"]
    clustered = result["target_clustered_bootstrap_95_ci_percentage_points"]
    assert example == pytest.approx([35.0, 54.0])
    assert example == pytest.approx(frozen["bootstrap_95_ci_percentage_points"])
    assert clustered[0] < example[0] and clustered[1] > example[1]
    assert result["target_sign_test"]["targets_marks_better"] == 7
    assert result["target_sign_test"]["two_sided_p_value"] == pytest.approx(2 / 2**7)


def test_committed_artifacts_match_regeneration(rows: list[dict[str, Any]]) -> None:
    result = mod.analyze(rows)
    assert (ROOT / mod.JSON_OUTPUT).read_text() == mod.serialize(result)
    assert (ROOT / mod.MD_OUTPUT).read_text() == mod.render_markdown(result)


def test_clustered_wider_with_reduced_resamples(rows: list[dict[str, Any]]) -> None:
    result = mod.analyze(rows, samples=500)
    ex = result["example_level_bootstrap_95_ci_percentage_points"]
    cl = result["target_clustered_bootstrap_95_ci_percentage_points"]
    assert cl[1] - cl[0] > ex[1] - ex[0]


def test_deterministic(rows: list[dict[str, Any]]) -> None:
    assert mod.serialize(mod.analyze(rows, samples=300)) == mod.serialize(
        mod.analyze(rows, samples=300)
    )


def test_harness_input_tokens_block(rows: list[dict[str, Any]]) -> None:
    block = mod.analyze(rows, samples=50)["harness_input_tokens"]
    assert block["raw"]["count"] == block["marks"]["count"] == 100
    assert round(block["raw"]["mean"]) == 11909
    assert round(block["marks"]["mean"]) == 11915
    committed = json.loads((ROOT / mod.JSON_OUTPUT).read_text())
    assert committed["harness_input_tokens"] == block


def test_rejects_missing_input_tokens(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    path = root / mod.PREDICTIONS_PATH
    lines = path.read_text().splitlines()
    record = json.loads(lines[0])
    del record["usage"]["input_tokens"]
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(mod.ClusteredAnalysisError, match="input_tokens"):
        mod.load_rows(root)


def test_interval_endpoints_rounded() -> None:
    committed = json.loads((ROOT / mod.JSON_OUTPUT).read_text())
    for key in (
        "example_level_bootstrap_95_ci_percentage_points",
        "target_clustered_bootstrap_95_ci_percentage_points",
    ):
        assert all(v == round(v, 9) for v in committed[key])
    assert committed["target_clustered_bootstrap_95_ci_percentage_points"] == [21.0, 67.0]


def test_structure(rows: list[dict[str, Any]]) -> None:
    result = mod.analyze(rows, samples=50)
    assert result["counts"]["example_count"] == 100
    assert len(result["per_target"]) == 10
    assert all(t["example_count"] == 10 for t in result["per_target"])
    assert sum(t["raw_correct"] for t in result["per_target"]) == 56


def test_markdown_numbers_come_from_json(rows: list[dict[str, Any]]) -> None:
    result = mod.analyze(rows, samples=50)
    result["target_clustered_bootstrap_95_ci_percentage_points"] = [-12.3, 98.7]
    md = mod.render_markdown(result)
    assert "[-12.3, +98.7]" in md


def test_clopper_pearson_and_sign_test_known_values() -> None:
    lo, hi = mod.clopper_pearson(44, 100)
    assert lo == pytest.approx(0.34084, abs=1e-4)
    assert hi == pytest.approx(0.54277, abs=1e-4)
    assert mod.clopper_pearson(0, 10)[0] == 0.0
    assert mod.clopper_pearson(10, 10)[1] == 1.0
    assert mod.sign_test_two_sided(0, 0) == 1.0
    assert mod.sign_test_two_sided(3, 3) == 1.0
    with pytest.raises(mod.ClusteredAnalysisError):
        mod.clopper_pearson(11, 10)


def test_percentile_matches_frozen_module() -> None:
    from pixelgym.grounding.analysis import _percentile

    values = sorted([0.1, 0.4, 0.2, 0.9, 0.5, 0.33])
    for q in (0.0, 0.025, 0.5, 0.975, 1.0):
        assert mod.percentile(values, q) == _percentile(values, q)
    with pytest.raises(mod.ClusteredAnalysisError):
        mod.percentile([], 0.5)


def test_bootstrap_rejects_empty_or_nonpositive() -> None:
    with pytest.raises(mod.ClusteredAnalysisError):
        mod.example_bootstrap([], 10, 1)
    with pytest.raises(mod.ClusteredAnalysisError):
        mod.cluster_bootstrap([[1]], 0, 1)


def test_rejects_prediction_disagreement(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    path = root / mod.PREDICTIONS_PATH
    lines = path.read_text().splitlines()
    record = json.loads(lines[0])
    record["correct"] = not record["correct"]
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(mod.ClusteredAnalysisError, match="disagree"):
        mod.load_rows(root)


def test_rejects_duplicate_prediction(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    path = root / mod.PREDICTIONS_PATH
    lines = path.read_text().splitlines()
    path.write_text("\n".join([*lines, lines[0]]) + "\n")
    with pytest.raises(mod.ClusteredAnalysisError, match="duplicate prediction"):
        mod.load_rows(root)


def test_rejects_missing_target_id_and_wrong_cluster_shape(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    path = root / mod.RESULTS_PATH
    original = json.loads(path.read_text())
    broken = json.loads(json.dumps(original))
    del broken["per_example"][0]["target_id"]
    path.write_text(json.dumps(broken))
    with pytest.raises(mod.ClusteredAnalysisError, match="target_id"):
        mod.load_rows(root)
    reshaped = json.loads(json.dumps(original))
    reshaped["per_example"][0]["target_id"] = "contact_email"
    path.write_text(json.dumps(reshaped))
    with pytest.raises(mod.ClusteredAnalysisError, match="targets x"):
        mod.load_rows(root)


def test_rejects_empty_results(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    (root / mod.RESULTS_PATH).write_text(json.dumps({"per_example": []}))
    with pytest.raises(mod.ClusteredAnalysisError, match="no per_example"):
        mod.load_rows(root)


def test_main_writes_both_outputs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert mod.main(["--repository-root", str(ROOT), "--output-dir", str(out)]) == 0
    assert (out / "grounding-clustered-analysis-v1.json").read_text() == (
        ROOT / mod.JSON_OUTPUT
    ).read_text()
