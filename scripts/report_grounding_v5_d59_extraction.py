"""Audit and analyze the completed parser-sensitivity campaign without provider calls.

The original replication exporter supplies the unchanged enforcement/accounting
audit. The original frozen analyzer supplies the statistical calculations. New
outputs live beside, and never replace, either predecessor's evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pixelgym.grounding.v5.action_extraction import ADAPTER_VERSION, PARSER_VERSION
from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.d59_extraction_successor import OUTPUT_DIRECTORY
from scripts import analyze_grounding_v5_d59 as frozen
from scripts import analyze_grounding_v5_d59_replication as replication_analysis
from scripts import report_grounding_v5_d59_replication as replication
from scripts.publish_pr196_calibration import ReadOnlyJournal

ROOT = replication.ROOT
PLAN = ROOT / OUTPUT_DIRECTORY / "execution-plan.json"
OUTPUT = ROOT / "artifacts/grounding-v5-d59-action-extraction-results"
KIND = "post_hoc_same_task_parser_sensitivity"


def receipt_audit(journal: ReadOnlyJournal, rows: list[dict]) -> dict:
    """Check stored hash/span/decision lineage, without decoding or rescoring actions."""
    events = journal.events()
    receipts, parsed, failures, completed, dispatches = {}, {}, {}, {}, []
    for event in events:
        key = (event.trial_id, event.step_index)
        if event.kind in {"action_extraction", "parsed_action_candidate"}:
            target = receipts if event.kind == "action_extraction" else parsed
            replication.require(key not in target, "duplicate extraction/parse receipt")
            target[key] = event
        elif event.kind == "sealed_unsuccessful_result":
            replication.require(
                event.payload["failure_code"] == "parse_failure", "non-parse failure"
            )
            replication.require(key not in failures, "duplicate parse failure")
            failures[key] = event
        elif event.kind == "attempt_completed":
            completed.setdefault(key, []).append(event)
        elif event.kind == "dispatch_committed":
            dispatches.append(event)
    replication.require(not (parsed.keys() & failures.keys()), "accepted and rejected same step")
    replication.require(
        receipts.keys() == parsed.keys() | failures.keys(), "parse receipt coverage"
    )
    replication.require(receipts.keys() == completed.keys(), "response receipt coverage")
    envelopes, decisions = Counter(), Counter()
    applied = 0
    for key, event in receipts.items():
        receipt = event.payload
        replication.require(
            receipt["adapter_version"] == ADAPTER_VERSION
            and receipt["parser_version"] == PARSER_VERSION,
            "extraction version mismatch",
        )
        matching = [
            e
            for e in completed[key]
            if e.payload["response_digest"] == receipt["canonical_response_digest"]
        ]
        replication.require(len(matching) == 1, "canonical response lineage mismatch")
        replication.require(matching[0].sequence < event.sequence, "receipt precedes response")
        raw = journal.get_object(
            receipt["canonical_response_digest"], expected_kind="canonical_provider_response"
        )
        replication.require(
            "sha256:" + sha256_bytes(raw) == receipt["canonical_response_digest"],
            "response object digest mismatch",
        )
        span = receipt.get("selected_character_span")
        if span is not None:
            text = json.loads(raw)["content"]
            replication.require(
                len(span) == 2
                and all(type(x) is int for x in span)
                and 0 <= span[0] <= span[1] <= len(text),
                "invalid extraction span",
            )
            replication.require(
                "sha256:" + sha256_bytes(text[span[0] : span[1]].encode())
                == receipt["selected_content_digest"],
                "selected content hash mismatch",
            )
        target = parsed.get(key, failures.get(key))
        replication.require(event.sequence < target.sequence, "parse predates extraction receipt")
        if key in parsed:
            replication.require(
                receipt["decision"] == "accepted"
                and receipt["candidate_digest"] == target.payload["candidate_digest"]
                and target.payload["parser_version"] == PARSER_VERSION,
                "accepted candidate receipt mismatch",
            )
        else:
            replication.require(receipt["decision"] == "rejected", "unsealed rejection")
        envelopes[receipt.get("envelope", "unselected")] += 1
        decisions[receipt["decision"]] += 1
        applied += bool(receipt["extraction_applied"])
    replication.require(
        len(dispatches) == sum(row["environment_actions"] for row in rows),
        "dispatch count mismatch",
    )
    for event in dispatches:
        key = (event.trial_id, event.step_index)
        replication.require(
            key in parsed and parsed[key].sequence < event.sequence,
            "dispatch lacks preceding accepted receipt",
        )
    return {
        "parse_receipts": len(receipts),
        "decisions": dict(sorted(decisions.items())),
        "envelopes": dict(sorted(envelopes.items())),
        "extraction_applied": applied,
        "dispatched": len(dispatches),
        "response_hashes_and_selected_spans_verified": True,
        "actions_reparsed_or_rescored": 0,
    }


def audit(directory: Path, archive: Path, backup: Path, *, root: Path = ROOT) -> dict:
    plan = replication.read(root / PLAN.relative_to(ROOT))
    replication.require(
        plan["execution_plan_digest"]
        == content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"})
        and plan["relationship"]["kind"] == KIND
        and plan["relationship"]["independent_confirmation"] is False,
        "invalid sensitivity plan",
    )
    result = replication.audit(directory, archive, backup, root=root, plan_path=PLAN)
    journal = ReadOnlyJournal(directory / "attempts.sqlite")
    try:
        extraction = receipt_audit(journal, result["results"])
    finally:
        journal.close()
    return {
        **result,
        "schema_version": "pixelgym-d59-extraction-public-results-v1",
        "result_kind": KIND,
        "independent_confirmation": False,
        "original_results_reclassified": 0,
        "extraction_audit": extraction,
    }


def analysis(root: Path = ROOT) -> dict:
    output = root / OUTPUT.relative_to(ROOT)
    projection, pilot = (
        replication.read(output / name) for name in ("report.json", "pilot-report.json")
    )
    plan_path = root / PLAN.relative_to(ROOT)
    plan = replication.read(plan_path)
    replication.require(
        plan["execution_plan_digest"]
        == content_digest({k: v for k, v in plan.items() if k != "execution_plan_digest"}),
        "invalid plan digest",
    )
    for report, phase in ((projection, "full"), (pilot, "pilot")):
        replication.require(
            report["execution_plan_digest"] == plan["execution_plan_digest"]
            and report["phase"] == phase
            and report["result_kind"] == KIND
            and report["runtime_identity"] == plan["runtime_identity"]
            and report["policy_ids"]
            == {m: p["policy_id"] for m, p in plan["policy_manifests"].items()}
            and report["source_artifacts"]["execution-plan.json"]
            == replication.sha256_file(plan_path),
            "projection identity/phase/plan mismatch",
        )
        replication.require(
            len(report["unresolved_timeouts"]) == report["unresolved_invocations"],
            "timeout disclosure count mismatch",
        )
    predecessor_path = root / plan["relationship"]["original_replication_results"]["path"]
    replication.require(
        replication.sha256_file(predecessor_path)
        == plan["relationship"]["original_replication_results"]["file_sha256"],
        "original results changed",
    )
    result = frozen.analyze(
        projection,
        plan,
        projection_sha256=replication.sha256_file(output / "report.json"),
        plan_sha256=replication.sha256_file(plan_path),
        analysis_plan_text=(root / frozen.ANALYSIS_PLAN).read_text(),
        design_doc_text=(root / frozen.DESIGN_DOC).read_text(),
    )
    result["schema_version"] = "pixelgym-d59-extraction-sensitivity-analysis-v1"
    result["result_kind"] = KIND
    result["independent_confirmation"] = False
    result["inputs"]["projection"]["path"] = (OUTPUT.relative_to(ROOT) / "report.json").as_posix()
    result["inputs"]["execution_plan"]["path"] = PLAN.relative_to(ROOT).as_posix()
    result["inputs"]["original_replication"] = plan["relationship"]["original_replication_results"]
    result["method"]["interpretation"] = (
        "Frozen calculations reused as post-hoc same-task sensitivity; no "
        "independent confirmatory claim."
    )
    policies = result["method"]["primary_comparison"]["policies"]
    policies["successor_frozen"] = {m: p["policy_id"] for m, p in plan["policy_manifests"].items()}
    policies["executed_match_successor_frozen"] = True
    result["sensitivity"] = result.pop("confirmatory")
    result["sensitivity"]["preregistered"] = False
    result["sensitivity"]["label"] = KIND
    result["reliability"]["preregistered"] = False
    result["reliability"]["note"] += " Inherited schedule on inspected tasks; sensitivity only."
    result["secondary"]["retained_journal_diagnostics"] = replication_analysis.secondary(
        projection["results"]
    )
    result["secondary"]["note"] = (
        "Termination categories retain their recorded labels; no terminal "
        "request failures occurred."
    )
    result["secondary"]["not_computable_from_public_projection"] = [
        {
            "diagnostic": "dependency retention, recovery, irreversible errors, path overhead, "
            "loops, per-request latency",
            "reason": "No new operational estimator is introduced; trace diagnostics remain "
            "unestimated as in the predecessor review.",
        },
        {
            "diagnostic": "complete provider token usage",
            "reason": (
                f"{projection['unresolved_invocations']} counted timeout attempts retain "
                "unknown usage; no imputation."
            ),
        },
    ]
    campaigns = {}
    for name, path in (
        ("historical_d59", frozen.OUTPUT_JSON),
        (
            "original_replication",
            replication.OUTPUT.relative_to(ROOT) / "confirmatory-analysis.json",
        ),
    ):
        prior = replication.read(root / path)
        campaigns[name] = {
            "path": path.as_posix(),
            "sha256": replication.sha256_file(root / path),
            "primary_rates": {
                m: r["overall"] for m, r in prior["success_rates"]["policies"].items()
            },
            "paired_difference": prior["confirmatory"]["paired_absolute_difference"]["estimate"],
        }
    result["predecessor_comparisons"] = campaigns
    result["disclosure"] = {
        "text": (
            "Post-hoc parser sensitivity on previously inspected tasks; not "
            "independent confirmation. "
            "Original results, policies, and denominators remain unchanged. Pilot "
            "excluded from all full denominators. "
            "All five confirmed-stopped post-send timeouts retain unknown provider "
            "completion and usage. "
            "Each received one counted same-request retry; both reservations "
            "remain. Login did not settle them. "
            "Strict identity and sandbox checks remained active. No model/auth "
            "call, task execution, action reparsing "
            "or response rescoring occurred in this audit/analysis. Token totals "
            "cover reported usage only. "
            "Subscription charge accounting is not a provider billing audit. "
            "Verified archive/backup copies share "
            "one local disk; no new restore drill or off-host backup. D5.10 and "
            "public wording remain human-owned."
        ),
        "unresolved_invocations": projection["unresolved_invocations"],
        "unresolved_timeouts": projection["unresolved_timeouts"],
        "stop_rule_changed_after_successor_results": False,
        "original_results_reclassified": 0,
    }
    result["accounting"] = {
        k: projection[k]
        for k in (
            "assigned",
            "completed",
            "stop_reason",
            "phase_usage",
            "provider_processes_started",
            "elapsed_seconds",
            "wall_elapsed_seconds",
            "authentication_wait_excluded_seconds",
            "approved_caps",
            "approved_runtime_window_hours",
            "retention",
            "journal_integrity",
        )
    }
    result["pilot"] = {
        "completed": pilot["completed"],
        "attempts": pilot["provider_processes_started"],
        "pooled_into_full": False,
    }
    result["extraction_audit"] = projection["extraction_audit"]
    result["human_gate"] = {"D5.10": None, "owner": "project owner"}
    return result


def render(result: dict) -> str:
    comparison = result["sensitivity"]
    table = comparison["paired_success_table"]
    boot = comparison["paired_absolute_difference"]
    lines = [
        "# D5.9 action-extraction sensitivity: D5.10 review",
        "",
        (
            "Generated from the audited, response-free projection using the frozen "
            "statistical functions. "
            "This is a post-hoc study on the same inspected tasks, not independent "
            "confirmation or a D5.10 verdict."
        ),
        "",
        "## Primary comparison",
        "",
        "| Campaign | History successes | Stateless successes | Difference |",
        "|---|---:|---:|---:|",
    ]
    for name, source in result["predecessor_comparisons"].items():
        rates = source["primary_rates"]
        lines.append(
            f"| [{name}](../../{source['path']}) | "
            f"{rates['history']['successes']}/{rates['history']['denominator']} | "
            f"{rates['stateless']['successes']}/{rates['stateless']['denominator']} | "
            f"{source['paired_difference']:.6%} |"
        )
    rates = result["success_rates"]["policies"]
    lines += [
        (
            f"| Parser sensitivity | {rates['history']['overall']['successes']}/192 | "
            f"{rates['stateless']['overall']['successes']}/192 | {boot['estimate']:.6%} |"
        ),
        "",
        (
            "These are separate campaigns. Cross-campaign differences are "
            "descriptive, not estimates of a causal parser effect. "
            "The original reporting rule and both original analyses remain unchanged."
        ),
        "",
        (
            f"Paired table: both success {table['both_success']}; "
            f"history only {table['history_only_success']}; "
            f"stateless only {table['stateless_only_success']}; "
            f"both failure {table['both_failure']}; {table['pairs']} pairs."
        ),
        "",
        (
            f"Two-sided exact McNemar p = {table['exact_mcnemar_two_sided_p']:.12g}. "
            f"History minus stateless = {boot['estimate']:.6%}; "
            "95% family-stratified logical-cluster "
            f"bootstrap interval [{boot['interval'][0]:.6%}, {boot['interval'][1]:.6%}]. "
            f"Frozen mechanics: seed {boot['seed']}, {boot['resamples']} resamples, "
            f"{boot['clusters']} clusters, {boot['strata']} strata. "
            "The inherited alpha 0.05 and 20-point minimum relevant difference are "
            "not a new confirmatory claim."
        ),
        "",
        "## Reliability and termination (separate denominators)",
        "",
        "| Phase | Mode | Success | Step limit | Invalid | Episodes |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for phase, modes in result["secondary"]["termination_profile"].items():
        for mode, counts in modes.items():
            lines.append(
                f"| {phase} | {mode} | {counts['success_termination']} | "
                f"{counts['step_limit_truncation']} | {counts['invalid_output']} | "
                f"{counts['denominator']} |"
            )
    lines += [
        "",
        (
            "The reliability JSON includes repeat consistency on the inherited "
            "representative schedule. "
            "Neither reliability repeats nor the four pilot assignments "
            "enter the 192 primary pairs."
        ),
        "",
        "## Retained diagnostics and accounting",
        "",
        (
            "| Phase | Mode | First critical correct / attempted | Reached both / "
            "episodes | Usage observed / attempts |"
        ),
        "|---|---|---:|---:|---:|",
    ]
    for phase, modes in result["secondary"]["retained_journal_diagnostics"].items():
        for mode, group in modes.items():
            choice = group["first_attempt_critical_decisions"]
            known = group["attempts_with_reported_usage"]
            total = known + group["attempts_without_reported_usage"]
            lines.append(
                f"| {phase} | {mode} | {choice['correct']}/{choice['denominator']} | "
                f"{group['reached_both_consumers']}/{group['episodes']} | {known}/{total} |"
            )
    accounting = result["accounting"]
    actions = sum(v["environment_actions"] for v in accounting["phase_usage"].values())
    lines += [
        "",
        (
            "Unreached consumers are excluded from this conditional first-attempt diagnostic; "
            "invalid choices at reached consumers are retained. "
            "This does not replace episode success. "
            "Reported token totals are in JSON; no timeout token use is imputed. "
            "Trace diagnostics without frozen estimators remain unestimated."
        ),
        "",
        (
            f"Full: {accounting['completed']}/{accounting['assigned']} completed; "
            f"{accounting['provider_processes_started']} attempts/wires, "
            f"{actions} actions, zero controls. Caps: {accounting['approved_caps']}; "
            f"{accounting['approved_runtime_window_hours']} active hours. "
            f"Active {accounting['elapsed_seconds']} s; "
            f"wall {accounting['wall_elapsed_seconds']} s; drained login wait excluded "
            f"{accounting['authentication_wait_excluded_seconds']} s."
        ),
        "",
        (
            f"Separate pilot: {result['pilot']['completed']} assignments, "
            f"{result['pilot']['attempts']} attempts; never pooled."
        ),
        "",
        (
            f"Stored extraction receipt audit: `{result['extraction_audit']}`. "
            "No actions were reparsed or rescored."
        ),
        "",
        "## Five unresolved timeouts",
        "",
        (
            "| Trial | Step | Attempt | Local process | Provider completion / "
            "usage | Counted attempts at action |"
        ),
        "|---|---:|---:|---|---|---:|",
    ]
    for fault in result["disclosure"]["unresolved_timeouts"]:
        lines.append(
            f"| `{fault['trial_id']}` | {fault['step_index']} | {fault['attempt_index']} | "
            f"confirmed stopped | unknown / unknown | {fault['counted_attempts_for_action']} |"
        )
    lines += [
        "",
        result["disclosure"]["text"],
        "",
        (
            "Both retained copies were rehashed and SQLite integrity checked. "
            "Archive snapshots precede the final "
            "retention receipt in the working summary. Raw responses, screenshots "
            "and local paths stay private. "
            "Public projections reproduce the statistics but cannot replay private raw evidence."
        ),
        "",
        (
            "D5.10 verdict: `null`. The owner decides the verdict, treatment of "
            "limitations, and any public wording."
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    export = commands.add_parser("audit")
    for name in ("input", "archive", "backup"):
        export.add_argument("--" + name, type=Path, required=True)
    export.add_argument("--verify", action="store_true")
    stats = commands.add_parser("analyze")
    stats.add_argument("--check", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.mode == "audit":
        reports = {
            phase: audit(args.input / phase, args.archive / phase, args.backup / phase)
            for phase in ("pilot", "full")
        }
        files = {
            ("report.json" if phase == "full" else "pilot-report.json"): json.dumps(
                report, indent=2, sort_keys=True
            )
            + "\n"
            for phase, report in reports.items()
        }
        check = args.verify
        observation = {
            p: {
                "completed": r["completed"],
                "unresolved_timeouts": r["unresolved_invocations"],
                "extraction_audit": r["extraction_audit"],
            }
            for p, r in reports.items()
        }
    else:
        result = analysis()
        files = {
            "sensitivity-analysis.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
            "sensitivity-analysis.md": render(result),
        }
        check = args.check
        observation = {"sensitivity": result["sensitivity"], "D5.10": None}
    for name, data in files.items():
        path = OUTPUT / name
        if check:
            replication.require(path.read_text() == data, f"stored evidence differs: {name}")
        else:
            path.write_text(data)
    print(json.dumps(observation, sort_keys=True))


if __name__ == "__main__":
    main()
