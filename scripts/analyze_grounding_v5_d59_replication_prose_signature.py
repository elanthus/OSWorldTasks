"""Exploratory diagnostic: shape of the parse failures in the retained D5.9 replication.

Reads the private attempt journal read-only, binds it to the audited public projection,
and emits counts only. No response text, screenshot, or private path leaves this script.
This is not a pre-registered test and does not alter the primary result or D5.10.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.report_grounding_v5_d59_replication import OUTPUT, require

JSON_NAME = "prose-signature-diagnostic.json"
MD_NAME = "prose-signature-diagnostic.md"
PHASES = ("primary", "reliability")
MODES = ("history", "stateless")
TYPING_EVENTS = ("text_input_focused", "invalid_text_value", "text_value_accepted")
FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)
KEYWORDS = {
    "mentions_code": re.compile(r"\bcode\b", re.IGNORECASE),
    "mentions_case_backspace_or_error": re.compile(
        r"lowercase|uppercase|capital|backspace|incorrect|mistake|error|wrong", re.IGNORECASE
    ),
    "references_history_frames": re.compile(
        r"image \d+|action history|previous (?:screenshot|frame|step)", re.IGNORECASE
    ),
}
DISCLAIMER = (
    "Exploratory diagnostic generated read-only from the retained attempt journal. It uses "
    "privileged dispatch diagnostics and the stored model text, so it is never pooled with the "
    "pre-registered primary comparison. Only counts are published; no response text, screenshot, "
    "or private path is included. No model was called. It attributes no cause to the historical "
    "and replication difference. D5.10 remains the owner's decision."
)


def open_journal(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_json(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return [
        tuple(json.loads(bytes(v)) if isinstance(v, (bytes, memoryview)) else v for v in row)
        for row in conn.execute(sql, params)
    ]


def fence_position(content: str) -> str:
    text = content.strip()
    fences = FENCE.findall(text)
    if not fences:
        return "no_fence"
    if text.startswith("```") and text.endswith("```") and len(fences) == 1:
        return "fence_only"
    if text.startswith("```"):
        return "fence_first_then_prose"
    if text.endswith("```"):
        return "prose_then_fence_last"
    return "fence_inside_prose"


def split_trial(trial_id: str) -> tuple[str, str]:
    parts = trial_id.split("-")
    require(len(parts) >= 6 and parts[0:3] == ["d59", "rep", parts[2]], "unexpected trial id")
    return parts[2], parts[4]


def analyze(conn: sqlite3.Connection, projection: dict[str, Any]) -> dict[str, Any]:
    plan_digests = {
        row[0]["execution_plan_digest"]
        for row in load_json(
            conn, "select payload from events where kind='d59_replication_assignment_started'"
        )
    }
    require(plan_digests == {projection["execution_plan_digest"]}, "journal plan digest mismatch")
    parser_versions = sorted(
        {
            row[0]["parser_version"]
            for row in load_json(
                conn, "select payload from events where kind='sealed_unsuccessful_result'"
            )
        }
    )
    public = {r["trial_id"]: r for r in projection["results"]}
    started = load_json(
        conn,
        "select trial_id, payload from events where kind='d59_replication_assignment_started'",
    )
    family = {trial: payload["job"]["seed_record"]["family"] for trial, payload in started}
    failures = load_json(
        conn,
        "select trial_id, step_index, attempt_index, payload from events "
        "where kind='sealed_unsuccessful_result' order by sequence",
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {
        (phase, mode): [] for phase in PHASES for mode in MODES
    }
    for trial, step, attempt, sealed in failures:
        phase, mode = split_trial(trial)
        require(trial in public, f"projection lacks {trial}")
        row = public[trial]
        require(row["classification"] == "invalid_output", f"projection disagrees for {trial}")
        ((completed,),) = load_json(
            conn,
            "select payload from events where kind='attempt_completed' and trial_id=? "
            "and step_index=? and attempt_index=?",
            (trial, step, attempt),
        )
        ((response,),) = load_json(
            conn, "select data from objects where digest=?", (completed["response_digest"],)
        )
        diagnostics = [
            payload["diagnostic"]
            for (payload,) in load_json(
                conn,
                "select payload from events where kind='privileged_dispatch_diagnostic' "
                "and trial_id=? order by step_index",
                (trial,),
            )
        ]
        content = response.get("content", "")
        groups[(phase, mode)].append(
            {
                "failure_code": sealed["failure_code"],
                "sanitized_reason": sealed["sanitized_reason"],
                "finish_reason": response.get("finish_reason"),
                "shape": fence_position(content),
                "action_objects_in_text": len(re.findall(r'"action_type"', content)),
                "single_rejected_attempt": row["model_attempts"] - row["environment_actions"] == 1,
                "last_event": diagnostics[-1]["event"] if diagnostics else None,
                "stage": row["final_stage_index"],
                "prior_invalid_text_value": any(
                    d["event"] == "invalid_text_value" for d in diagnostics
                ),
                "family": family[trial],
                "terminal_output_tokens": completed["usage"].get("output_tokens"),
                **{name: bool(rx.search(content)) for name, rx in KEYWORDS.items()},
            }
        )
    typing: dict[str, dict[str, int]] = {}
    for mode in MODES:
        counter: Counter[str] = Counter()
        for (payload,) in load_json(
            conn,
            "select payload from events where kind='privileged_dispatch_diagnostic' "
            f"and trial_id like 'd59-rep-primary-%-{mode}-%'",
        ):
            event = payload["diagnostic"]["event"]
            if event in TYPING_EVENTS:
                counter[event] += 1
        typing[mode] = dict(sorted(counter.items()))
    summary: dict[str, Any] = {}
    for (phase, mode), rows in groups.items():
        expected = sum(
            1
            for r in projection["results"]
            if r["phase"] == phase and r["mode"] == mode and r["classification"] == "invalid_output"
        )
        require(len(rows) == expected, f"invalid-output count mismatch for {phase}/{mode}")
        tokens = sorted(r["terminal_output_tokens"] or 0 for r in rows)
        summary.setdefault(phase, {})[mode] = {
            "invalid_output_episodes": len(rows),
            "single_rejected_attempt": sum(r["single_rejected_attempt"] for r in rows),
            "failure_reason": dict(
                Counter(f"{r['failure_code']}:{r['sanitized_reason']}" for r in rows)
            ),
            "finish_reason": dict(Counter(str(r["finish_reason"]) for r in rows)),
            "response_shape": dict(Counter(r["shape"] for r in rows)),
            "exactly_one_action_object": sum(r["action_objects_in_text"] == 1 for r in rows),
            "keywords": {name: sum(r[name] for r in rows) for name in KEYWORDS},
            "last_committed_event": dict(Counter(str(r["last_event"]) for r in rows)),
            "failures_after_typing_event": sum(r["last_event"] in TYPING_EVENTS for r in rows),
            "final_stage_index": {
                str(k): v for k, v in sorted(Counter(r["stage"] for r in rows).items())
            },
            "prior_invalid_text_value": sum(r["prior_invalid_text_value"] for r in rows),
            "family": dict(sorted(Counter(r["family"] for r in rows).items())),
            "terminal_output_tokens": {
                "min": tokens[0] if tokens else None,
                "median": tokens[len(tokens) // 2] if tokens else None,
                "max": tokens[-1] if tokens else None,
            },
        }
    return {
        "schema_version": "pixelgym-agent-v5-d59-replication-prose-signature-v1",
        "execution_plan_digest": projection["execution_plan_digest"],
        "parser_versions": parser_versions,
        "parser_rule": "bare JSON or one complete JSON fence; prose around the action is rejected",
        "typing_diagnostics_primary": typing,
        "by_phase_and_mode": summary,
        "disclaimer": DISCLAIMER,
    }


def fmt(counts: dict[str, Any]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def render(result: dict[str, Any]) -> str:
    lines = [
        "# D5.9 replication: parse-failure signature (exploratory)",
        "",
        (
            f"Generated by `scripts/{Path(__file__).name}` from the retained attempt journal "
            "and [report.json](report.json). "
            f"Execution plan `{result['execution_plan_digest']}`. "
            f"Parser: `{'`, `'.join(result['parser_versions'])}` ({result['parser_rule']})."
        ),
        "",
        DISCLAIMER,
        "",
        "## Terminal parse failures by phase and arm",
        "",
        (
            "| Phase | Arm | Episodes | One rejected attempt | Reasons | Response shape "
            "| Exactly one action object | finish_reason |"
        ),
        "|---|---|---:|---:|---|---|---:|---|",
    ]
    for phase, arms in result["by_phase_and_mode"].items():
        for mode, s in arms.items():
            lines.append(
                f"| {phase} | {mode} | {s['invalid_output_episodes']} | "
                f"{s['single_rejected_attempt']} | {fmt(s['failure_reason'])} | "
                f"{fmt(s['response_shape'])} | {s['exactly_one_action_object']} | "
                f"{fmt(s['finish_reason'])} |"
            )
    lines += [
        "",
        "## Context of the primary-phase failures",
        "",
        (
            "| Arm | After a typing event | Prior invalid text value | Mentions code "
            "| Case/backspace/error | References history frames | Stage index counts |"
        ),
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for mode, s in result["by_phase_and_mode"]["primary"].items():
        k = s["keywords"]
        stages = ", ".join(f"{a}:{b}" for a, b in s["final_stage_index"].items())
        lines.append(
            f"| {mode} | {s['failures_after_typing_event']} | {s['prior_invalid_text_value']} | "
            f"{k['mentions_code']} | {k['mentions_case_backspace_or_error']} | "
            f"{k['references_history_frames']} | {stages} |"
        )
    lines += ["", "Last committed diagnostic event before the rejected response (primary):", ""]
    for mode, s in result["by_phase_and_mode"]["primary"].items():
        lines.append(f"- {mode}: {fmt(dict(sorted(s['last_committed_event'].items())))}")
    lines += ["", "Workflow family of the failed episodes (primary):", ""]
    for mode, s in result["by_phase_and_mode"]["primary"].items():
        lines.append(f"- {mode}: {fmt(s['family'])}")
    lines += ["", "Typing diagnostics over all primary episodes:", ""]
    for mode, events in result["typing_diagnostics_primary"].items():
        lines.append(f"- {mode}: {fmt(events)}")
    lines += [
        "",
        "## Reading",
        "",
        (
            "Every rejected response is a stop-terminated reply carrying one well-formed action "
            "object; what the parser rejects is prose around it. The parser rule is unchanged "
            "from the historical D5.9 code revision, so stricter parsing does not explain the "
            "difference between campaigns. Why the same pinned model emitted prose more often "
            "under the history prompt is not established here; the historical raw journals do "
            "not exist, so the same breakdown cannot be produced for the historical campaign."
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True, help="private attempts.sqlite")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    projection = json.loads((args.output / "report.json").read_text())
    with open_journal(args.journal) as conn:
        result = analyze(conn, projection)
    files = {
        JSON_NAME: json.dumps(result, indent=2, sort_keys=True) + "\n",
        MD_NAME: render(result),
    }
    for name, data in files.items():
        path = args.output / name
        if args.check:
            require(path.read_text() == data, f"stored diagnostic differs: {name}")
        else:
            path.write_text(data)
    print(json.dumps(result["by_phase_and_mode"]["primary"], sort_keys=True))


if __name__ == "__main__":
    main()
