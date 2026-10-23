"""Offline checks of successor lineage, frozen arithmetic and disclosure boundaries."""

from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest

from pixelgym.grounding.v5.action_extraction import ADAPTER_VERSION, PARSER_VERSION
from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from scripts import report_grounding_v5_d59_extraction as report
from scripts.generate_d510_evidence_report import scan_directory


def test_frozen_statistics_reproduce_without_pooling_or_confirmatory_claim():
    result = report.analysis()
    stored = report.replication.read(report.OUTPUT / "sensitivity-analysis.json")
    assert result == stored
    assert report.render(result) == (report.OUTPUT / "sensitivity-analysis.md").read_text()
    assert "confirmatory" not in result
    assert result["sensitivity"]["preregistered"] is False
    assert result["independent_confirmation"] is False
    table = result["sensitivity"]["paired_success_table"]
    assert [
        table[k]
        for k in (
            "pairs",
            "both_success",
            "history_only_success",
            "stateless_only_success",
            "both_failure",
        )
    ] == [192, 15, 117, 5, 55]
    # Independent binomial-tail arithmetic, rather than the production estimator.
    assert table["exact_mcnemar_two_sided_p"] == pytest.approx(
        2 * sum(math.comb(122, k) for k in range(6)) / 2**122, rel=1e-12, abs=0
    )
    boot = result["sensitivity"]["paired_absolute_difference"]
    assert boot["estimate"] == 112 / 192
    assert boot["interval"] == pytest.approx([0.5080213903743316, 0.6580310880829016])
    assert (boot["seed"], boot["resamples"], boot["rows"], boot["clusters"], boot["strata"]) == (
        20260911,
        10000,
        192,
        168,
        6,
    )
    assert result["reliability"]["preregistered"] is False
    assert result["pilot"] == {"completed": 4, "attempts": 97, "pooled_into_full": False}
    assert result["human_gate"]["D5.10"] is None
    assert (
        result["predecessor_comparisons"]["original_replication"]["paired_difference"] == 42 / 192
    )
    assert result["predecessor_comparisons"]["historical_d59"]["paired_difference"] == 91 / 192


def test_five_unknown_timeouts_are_retained_and_never_imputed():
    projection = report.replication.read(report.OUTPUT / "report.json")
    assert projection["completed"] == len(projection["results"]) == 432
    assert projection["provider_processes_started"] == 11334
    assert sum(r["attempts_with_reported_usage"] for r in projection["results"]) == 11329
    assert projection["unresolved_invocations"] == len(projection["unresolved_timeouts"]) == 5
    assert {t["trial_id"] for t in projection["unresolved_timeouts"]} == {
        "d59-extract-primary-6058-history-r0",
        "d59-extract-primary-6111-history-r0",
        "d59-extract-primary-6130-history-r0",
        "d59-extract-primary-6145-history-r0",
        "d59-extract-reliability-6034-history-r2",
    }
    for timeout in projection["unresolved_timeouts"]:
        assert timeout["process_confirmed_stopped"] is True
        assert timeout["provider_completion"] == timeout["provider_usage"] == "unknown"
        assert timeout["counted_attempts_for_action"] == 2
        assert timeout["retry_request_digest_matches"] is True
    assert projection["extraction_audit"]["decisions"] == {"accepted": 11326, "rejected": 3}
    assert projection["extraction_audit"]["actions_reparsed_or_rescored"] == 0
    scan = scan_directory(report.OUTPUT)
    assert not any(scan["finding_counts"].values()), scan


def fixture():
    raw = json.dumps({"content": "prefix\n{}\nsuffix"}).encode()
    digest = "sha256:" + sha256_bytes(raw)

    def event(sequence, kind, **payload):
        return SimpleNamespace(
            sequence=sequence, kind=kind, trial_id="t", step_index=0, payload=payload
        )

    receipt = {
        "adapter_version": ADAPTER_VERSION,
        "parser_version": PARSER_VERSION,
        "canonical_response_digest": digest,
        "decision": "accepted",
        "extraction_applied": True,
        "envelope": "single_json_fence",
        "selected_character_span": [7, 9],
        "selected_content_digest": "sha256:" + sha256_bytes(b"{}"),
        "candidate_digest": content_digest({"action": "fixture"}),
    }
    events = [
        event(1, "attempt_completed", response_digest=digest),
        event(2, "action_extraction", **receipt),
        event(
            3,
            "parsed_action_candidate",
            candidate_digest=receipt["candidate_digest"],
            parser_version=PARSER_VERSION,
        ),
        event(4, "dispatch_committed"),
    ]

    class Journal:
        def events(self):
            return events

        def get_object(self, observed, *, expected_kind):
            assert observed == digest and expected_kind == "canonical_provider_response"
            return raw

    return events, Journal()


def test_receipt_audit_checks_hash_span_and_order_without_reparsing():
    _, journal = fixture()
    assert report.receipt_audit(journal, [{"environment_actions": 1}]) == {
        "parse_receipts": 1,
        "decisions": {"accepted": 1},
        "envelopes": {"single_json_fence": 1},
        "extraction_applied": 1,
        "dispatched": 1,
        "response_hashes_and_selected_spans_verified": True,
        "actions_reparsed_or_rescored": 0,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "version",
        "lineage",
        "span",
        "hash",
        "candidate",
        "order",
        "decision",
    ],
)
def test_receipt_corruption_is_rejected(mutation):
    events, journal = fixture()
    receipt = events[1].payload
    if mutation == "missing":
        events.pop(1)
    elif mutation == "duplicate":
        events.append(copy.deepcopy(events[1]))
    elif mutation == "version":
        receipt["adapter_version"] = "other"
    elif mutation == "lineage":
        receipt["canonical_response_digest"] = "other"
    elif mutation == "span":
        receipt["selected_character_span"] = [-1, 3]
    elif mutation == "hash":
        receipt["selected_content_digest"] = "wrong"
    elif mutation == "candidate":
        receipt["candidate_digest"] = "wrong"
    elif mutation == "order":
        events[1].sequence = 5
    elif mutation == "decision":
        receipt["decision"] = "rejected"
    with pytest.raises(ValueError):
        report.receipt_audit(journal, [{"environment_actions": 1}])


@pytest.mark.parametrize("tamper", ["phase", "policy", "identity", "plan", "timeouts"])
def test_analysis_rejects_changed_projection_bindings(tmp_path, tamper):
    output = tmp_path / report.OUTPUT.relative_to(report.ROOT)
    output.mkdir(parents=True)
    for name in ["report.json", "pilot-report.json"]:
        data = report.replication.read(report.OUTPUT / name)
        if name == "report.json":
            if tamper == "phase":
                data["phase"] = "pilot"
            elif tamper == "policy":
                data["policy_ids"]["history"] = "other"
            elif tamper == "identity":
                data["runtime_identity"]["auth_method"] = "claude.ai"
            elif tamper == "plan":
                data["source_artifacts"]["execution-plan.json"] = "wrong"
            elif tamper == "timeouts":
                data["unresolved_timeouts"].pop()
        (output / name).write_text(json.dumps(data))
    plan = tmp_path / report.PLAN.relative_to(report.ROOT)
    plan.parent.mkdir(parents=True)
    plan.write_bytes(report.PLAN.read_bytes())
    with pytest.raises(ValueError):
        report.analysis(tmp_path)
