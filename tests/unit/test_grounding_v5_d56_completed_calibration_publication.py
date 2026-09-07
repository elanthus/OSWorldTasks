from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

import scripts.publish_grounding_v5_d56_completed_calibrations as publication
from scripts.publish_grounding_v5_d56_completed_calibrations import (
    AUDIT_PATH,
    DERIVATIVE_PATH,
    ERRATA_PATH,
    EXPECTED_MAIN_SUMMARIES,
    RELATION_PATH,
    REPORT_PATH,
    RUN_SPECS,
    PublicationError,
    _attach_relation_checks,
    _classification_counts,
    _file_digest,
    _load_json,
    _redaction_is_safe,
    _source_audits,
    _spend_record,
    _taxonomy_record,
    audit_run,
    build_derivative,
    build_relation_sources,
    inventory_main,
    publish,
    render_report,
    validate_relation,
    verify,
)

ROOT = Path(__file__).parents[2]


def _keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            result.add(str(key))
            result.update(_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_keys(child))
    return result


def test_publication_is_reproducible_without_provider_calls() -> None:
    result = verify(ROOT)

    assert result["provider_calls_made"] == 0
    assert result["artifacts_reproducible"] is True
    assert tuple(result["main_full_calibration_summaries"]) == EXPECTED_MAIN_SUMMARIES
    assert result["publication"] == {
        "checks_failed_for_published_runs": 0,
        "milestone_gate_verdict": "not_evaluated_human_owned",
        "published_run_ids": ["gemini-v3b", "qwen-v3"],
    }


def test_inventory_falls_back_to_head_in_detached_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        publication,
        "_git_ref_exists",
        lambda repository_root, ref: ref == "HEAD",
    )

    assert publication._inventory_ref(ROOT) == "HEAD"


def test_inventory_prefers_origin_main_and_allows_future_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publication, "_git_ref_exists", lambda repository_root, ref: True)
    assert publication._inventory_ref(ROOT) == "refs/remotes/origin/main"

    future_summary = (
        "artifacts/grounding-v5-d56-future-full-calibration-run/summary.json"
    )
    monkeypatch.setattr(
        publication,
        "_git",
        lambda repository_root, *args: "\n".join(
            (*EXPECTED_MAIN_SUMMARIES, future_summary)
        ),
    )
    inventory = inventory_main(ROOT)
    assert set(EXPECTED_MAIN_SUMMARIES) <= set(inventory)
    assert future_summary in inventory


def test_completed_table_preserves_all_terminal_classifications_and_spend() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    by_run = {row["run_id"]: row for row in derivative["runs"]}

    assert tuple(by_run) == ("gemini-v3b", "qwen-v3")
    assert by_run["gemini-v3b"]["classification_counts"] == {
        "attempted": 50,
        "infrastructure_failure": 0,
        "invalid_output": 1,
        "policy_violation": 0,
        "request_failure": 1,
        "success": 35,
        "truncation": 13,
    }
    assert by_run["gemini-v3b"]["spend"] == {
        "budget_accounted_run_spend_usd": "6.901905750",
        "known_run_spend_usd": "4.911249750",
        "maximum_run_spend_usd": "7.000000000",
        "remaining_run_spend_usd": "0.098094250",
        "reservation_basis": "per_request_theoretical_maximum",
        "transport_rows_without_cost": 20,
        "unknown_charge_outcomes": 20,
        "unknown_charge_reservation_usd": "1.990656000",
    }
    assert by_run["qwen-v3"]["classification_counts"] == {
        "attempted": 50,
        "infrastructure_failure": 0,
        "invalid_output": 3,
        "policy_violation": 0,
        "request_failure": 0,
        "success": 0,
        "truncation": 47,
    }
    assert by_run["qwen-v3"]["spend"]["unknown_charge_reservation_usd"] == (
        "0.000000000"
    )


def test_policy_versions_publish_shared_partition_and_comparability_limits() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    by_run = {row["run_id"]: row for row in derivative["runs"]}
    gemini = by_run["gemini-v3b"]["versions"]
    qwen = by_run["qwen-v3"]["versions"]

    assert gemini["calibration_partition_manifest_digest"] == qwen[
        "calibration_partition_manifest_digest"
    ]
    assert gemini["policy_manifest_digest"] != qwen["policy_manifest_digest"]
    assert gemini["code_revision"] != qwen["code_revision"]
    assert gemini["runtime_digest"] != qwen["runtime_digest"]
    assert gemini["temperature"] is None
    assert qwen["temperature"] == "0"
    assert gemini["response_validation"]["upstream_response_format"] == "json_schema"
    assert qwen["response_validation"] is None
    assert any("policy-manifest digests" in item for item in derivative["limitations"])
    assert any("temperature 0" in item for item in derivative["limitations"])


def test_incomplete_runs_are_excluded_with_failed_checks_disclosed() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    excluded = {row["run_id"]: row for row in derivative["unpublished_retained_runs"]}

    assert excluded["qwen-v2"]["classification_counts"]["attempted"] == 13
    assert excluded["qwen-v2"]["failed_checks"] == ["completed_assigned_denominator"]
    assert excluded["gemini-v3"]["classification_counts"]["attempted"] == 5
    assert excluded["gemini-v3"]["failed_checks"] == [
        "completed_assigned_denominator",
        "publication_relation_verified",
    ]


def test_a_failed_completed_run_cannot_enter_the_derivative() -> None:
    audits = _source_audits(ROOT)
    candidate = next(row for row in audits if row["run_id"] == "gemini-v3b")
    candidate["checks"]["spend_and_unknown_reservations_reconcile"] = False
    candidate["source_checks_passed"] = False

    with pytest.raises(PublicationError, match="publication set"):
        build_derivative(audits)


def test_relation_check_is_derived_from_the_relation_source() -> None:
    audits = _source_audits(ROOT)
    relation = build_relation_sources(ROOT, audits)
    relation["authoritative"]["runs"][0]["summary_file_sha256"] = "sha256:bad"

    _attach_relation_checks(ROOT, audits, relation)

    gemini = next(row for row in audits if row["run_id"] == "gemini-v3b")
    assert gemini["checks"]["publication_relation_verified"] is False
    with pytest.raises(PublicationError, match="publication set"):
        build_derivative(audits)


def test_publish_builds_every_output_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writes: list[Path] = []
    monkeypatch.setattr(publication, "DERIVATIVE_PATH", Path("derivative.json"))
    monkeypatch.setattr(publication, "REPORT_PATH", Path("report.md"))
    monkeypatch.setattr(publication, "RELATION_PATH", Path("relation.json"))
    monkeypatch.setattr(publication, "AUDIT_PATH", Path("audit.json"))
    monkeypatch.setattr(
        publication,
        "_build_outputs",
        lambda repository_root: (_ for _ in ()).throw(PublicationError("audit failed")),
    )
    monkeypatch.setattr(
        publication, "_write_new", lambda path, text: writes.append(path)
    )

    with pytest.raises(PublicationError, match="audit failed"):
        publish(tmp_path)

    assert writes == []


def test_unknown_reservation_mismatch_is_detected() -> None:
    spec = next(spec for spec in RUN_SPECS if spec.run_id == "gemini-v3b")
    plan = _load_json(ROOT / spec.plan_path)
    summary = copy.deepcopy(_load_json(ROOT / spec.summary_path))
    errata = _load_json(ROOT / ERRATA_PATH)
    summary["unknown_charge_reservation_usd"] = "0"

    _, reconciles = _spend_record(ROOT, spec, plan, summary, errata)

    assert reconciles is False


def test_fault_taxonomy_bound_is_derived_from_transport_and_rows() -> None:
    spec = next(spec for spec in RUN_SPECS if spec.run_id == "gemini-v3b")
    plan = _load_json(ROOT / spec.plan_path)
    summary = _load_json(ROOT / spec.summary_path)
    counts = _classification_counts(summary)

    record, verified = _taxonomy_record(plan, summary, counts)

    assert verified is True
    assert record["legacy_cli_process_failures_recorded_as_invalid_output"] == 0
    assert record["inputs"] == {
        "episode_rows_checked": 50,
        "episode_rows_with_cli_fault_key": 0,
        "http_openrouter_transport": True,
        "invalid_output_rows": 1,
        "manifest_provider_alias": "openrouter/google-vertex/global",
        "provider_endpoint": "https://openrouter.ai",
        "provider_name": "openrouter",
        "transport_rows_checked": len(summary["transport_records"]),
        "transport_rows_with_cli_fault_key": 0,
    }

    changed = copy.deepcopy(summary)
    changed["episode_results"][0]["cli_fault"] = True
    changed_record, changed_verified = _taxonomy_record(plan, changed, counts)
    assert changed_verified is False
    assert changed_record["legacy_cli_process_failures_recorded_as_invalid_output"] is None


def test_unexpected_classification_is_a_named_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = next(spec for spec in RUN_SPECS if spec.run_id == "gemini-v3b")
    original_load = publication._load_json
    changed = copy.deepcopy(original_load(ROOT / spec.summary_path))
    prior = changed["episode_results"][0]["classification"]
    changed["episode_results"][0]["classification"] = "unexpected_terminal"
    changed["classifications"][prior] -= 1
    changed["classifications"]["unexpected_terminal"] = 1

    def load_with_unknown(path: Path) -> dict[str, object]:
        if path == ROOT / spec.summary_path:
            return changed
        return original_load(path)

    monkeypatch.setattr(publication, "_load_json", load_with_unknown)
    row = audit_run(ROOT, spec, errata=original_load(ROOT / ERRATA_PATH))

    assert row["checks"]["classification_keys_recognized"] is False


def test_unknown_outcome_classification_populates_infrastructure_count() -> None:
    summary = {
        "episode_results": [
            {"classification": "unknown_outcome_infrastructure_failure"}
        ]
    }

    assert _classification_counts(summary) == {
        "attempted": 1,
        "infrastructure_failure": 1,
        "invalid_output": 0,
        "policy_violation": 0,
        "request_failure": 0,
        "success": 0,
        "truncation": 0,
    }


def test_predecessor_disclosures_are_carried_without_changing_current_counts() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    by_run = {row["run_id"]: row for row in derivative["runs"]}

    assert by_run["gemini-v3b"]["predecessor_disclosures"]["policy_predecessor"]
    qwen_predecessor = by_run["qwen-v3"]["predecessor_disclosures"][
        "frozen_infrastructure_predecessor"
    ]
    assert qwen_predecessor["attempted_policy_task_pairs"] == 1
    assert qwen_predecessor["terminal"]["http_status"] == 429
    assert qwen_predecessor["terminal"]["classification"] == (
        "unknown_outcome_infrastructure_failure"
    )
    assert by_run["qwen-v3"]["classification_counts"]["infrastructure_failure"] == 0


def test_relation_binds_sources_and_excludes_both_restricted_journals() -> None:
    relation = _load_json(ROOT / RELATION_PATH)

    assert validate_relation(ROOT, relation) is True
    assert [row["run_id"] for row in relation["authoritative"]["runs"]] == [
        "gemini-v3b",
        "qwen-v3",
    ]
    exclusions = relation["excluded_authoritative_artifacts"]
    assert [row["path"] for row in exclusions] == [
        "artifacts/grounding-v5-d56-gemini-v3b-full-calibration-run/attempts.sqlite",
        "artifacts/grounding-v5-d56-qwen-v3-full-calibration-run/attempts.sqlite",
    ]
    assert all(row["git_status"] == "must_not_commit" for row in exclusions)
    assert all(row["sha256"] is None and row["size_bytes"] is None for row in exclusions)
    for row in exclusions:
        assert subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", "--", row["path"]],
            cwd=ROOT,
            check=False,
        ).returncode == 0
        assert subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", row["path"]],
            cwd=ROOT,
            check=False,
            capture_output=True,
        ).returncode != 0


def test_outputs_are_redacted_and_report_is_derivative_only() -> None:
    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    rendered = render_report(derivative, derivative_sha256=_file_digest(ROOT / DERIVATIVE_PATH))
    calibration_table = rendered.split("## Calibration table", 1)[1].split(
        "## Policy identity", 1
    )[0]

    assert rendered == (ROOT / REPORT_PATH).read_text(encoding="utf-8")
    assert calibration_table.count("| `A-gemini-stateful-v3` |") == 1
    assert calibration_table.count("| `B-qwen-stateful-v3` |") == 1
    assert "35 (70.0%)" in rendered
    assert "0 (0.0%)" in rendered
    assert "`per-run-ledger-wording`: Each listed plan used an independent ledger" in rendered
    assert "`gemini-policy-generation-label`: Both plans and their summaries" in rendered
    forbidden = {
        "checkpoint",
        "content",
        "image",
        "prompt",
        "raw_request",
        "raw_response",
        "request_body",
        "response_body",
        "screenshot",
    }
    for path in (DERIVATIVE_PATH, RELATION_PATH, AUDIT_PATH, REPORT_PATH):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "Bearer " not in text
        assert "data:image/" not in text
    assert not (forbidden & _keys(derivative))


def test_redaction_scan_rejects_payloads_paths_and_credentials() -> None:
    assert _redaction_is_safe({"safe": True}, {"content": "provider text"}) is False
    assert _redaction_is_safe({"safe": True}, {"path": "/Users/operator/run"}) is False
    assert _redaction_is_safe({"safe": True}, {"value": "Bearer abcdefghijklmnop"}) is False


def test_readme_numbers_trace_to_generated_derivative() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## v5 agent benchmark (in progress)", 1)[1].split(
        "## Architecture", 1
    )[0]

    derivative = _load_json(ROOT / DERIVATIVE_PATH)
    for run in derivative["runs"]:
        counts = run["classification_counts"]
        spend = run["spend"]
        percentage = 100 * counts["success"] / counts["attempted"]
        expected = (
            f"| `{run['slot']}` | `{run['provider_alias']}` / `{run['model_alias']}` | "
            f"{run['assigned_tasks']} | {counts['attempted']} | "
            f"{counts['success']} ({percentage:.1f}%) | {counts['invalid_output']} | "
            f"{counts['request_failure']} | {counts['infrastructure_failure']} | "
            f"{counts['policy_violation']} | {counts['truncation']} | "
            f"${spend['unknown_charge_reservation_usd']} "
            f"({spend['unknown_charge_outcomes']} outcomes) |"
        )
        assert expected in section
    assert "no v5 result is a benchmark score or a\nmilestone-gate verdict" in section
    assert "`A-gemini-stateful-v2` calibration remains **withdrawn**" in section
    assert DERIVATIVE_PATH.as_posix() in section
