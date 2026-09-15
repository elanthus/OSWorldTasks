"""Audit restricted PR196 journals and publish response-free calibration evidence.

Export requires a local JSON mapping of cohort IDs to directories. Verification and
report generation read only the committed snapshots and never invoke a provider.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import threading
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pixelgym.grounding.v5.claude_code_policy import ClaudeInvocationJournal
from pixelgym.grounding.v5.codex_cli_policy import CodexCliInvocationJournal
from pixelgym.grounding.v5.contracts import content_digest, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import episode_measurements
from pixelgym.grounding.v5.memory_generator import generate_memory_task

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "artifacts/grounding-v5-pr196-calibration"
FROZEN = "1e5d9c0d19acf51505919deefe0d155c2ab22b26"
COHORTS = (
    "luna-original",
    "luna-continuation",
    "haiku-v4",
    "haiku-cli-continuation",
    "haiku-openrouter",
    "haiku-v1",
    "haiku-v2",
    "haiku-v3",
)
CHAINS = {"luna": COHORTS[:2], "haiku": COHORTS[2:5]}
TERMINAL = {"success_termination", "step_limit_truncation", "invalid_output"}
INTERRUPTED = {"infrastructure_failure", "phase_time_stop", "request_failure"}
BENCHMARK_FILES = (
    "memory_generator.py",
    "memory_backend.py",
    "memory_focus_backend.py",
    "screenshot_memory.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def encoded(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class ReadOnlyJournal(V5AttemptJournal):
    """Reuse the stored-object readers without schema initialization or writes."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)

    def integrity_report(self):
        # Stream multi-gigabyte objects rather than materializing them together.
        count = 0
        for digest, data in self._connection.execute("SELECT digest,data FROM objects"):
            require("sha256:" + sha256_bytes(bytes(data)) == digest, "object digest mismatch")
            count += 1
        version = self._digest_version()
        rows = []
        for event in self.events():
            row = {
                "sequence": event.sequence,
                "event_key": event.event_key,
                "kind": event.kind,
                "payload": event.payload,
            }
            if version == "v2":
                row.update(
                    trial_id=event.trial_id,
                    step_index=event.step_index,
                    attempt_index=event.attempt_index,
                )
            rows.append(row)
        result = {
            "schema_version": "pixelgym-agent-v5-journal-integrity-v1",
            "object_count": count,
            "event_count": len(rows),
            "event_chain_digest": content_digest(rows),
        }
        if version == "v2":
            result["digest_version"] = version
        return result


def audit(directory, name):
    plan, summary = read(directory / "execution-plan.json"), read(directory / "summary.json")
    require(content_digest(plan) == summary["plan_digest"], "plan binding mismatch")
    require(summary["frozen_benchmark_revision"] == FROZEN, "benchmark revision mismatch")
    require(summary.get("error") is None, "cohort has an unreviewed execution error")
    revision = plan["adapter_revision"]
    source_hashes = {}
    for filename in BENCHMARK_FILES:
        path = "pixelgym/grounding/v5/" + filename
        source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
        frozen = subprocess.check_output(["git", "show", f"{FROZEN}:{path}"], cwd=ROOT)
        require(source == frozen, "benchmark source changed: " + path)
        require(
            (ROOT / path).read_bytes() == frozen, "audit implementation differs from frozen source"
        )
        source_hashes[path] = "sha256:" + sha256_bytes(source)
    with _journal_context(directory / "attempts.sqlite") as journal:
        integrity = journal.integrity_report()
        require(integrity == summary["journal_integrity"], "journal integrity mismatch")
        events = journal.events()
        results = [e.payload for e in events if e.kind == "cli_memory_assignment_completed"]
        require(results == summary["results"], "recorded result mismatch")
        require(len(results) == summary["completed"], "completed count mismatch")
        jobs = {(j["seed"], j["mode"]): j for j in plan["jobs"]}
        require(len(jobs) == len(plan["jobs"]), "duplicate assignments")
        seen = set()
        for row in results:
            key = (row["seed"], row["mode"])
            require(key in jobs and key not in seen, "duplicate or unknown result")
            seen.add(key)
            task = generate_memory_task(row["seed"])
            require(
                task.task_id == row["task_id"] == jobs[key]["task_id"], "task identity mismatch"
            )
            require(
                content_digest(task.canonical_dict())
                == row["task_digest"]
                == jobs[key]["task_digest"],
                "task digest mismatch",
            )
            require(
                task.max_episode_steps == row["action_limit"] == jobs[key]["action_limit"],
                "action cap mismatch",
            )
            measurements = episode_measurements(journal, row["trial_id"], row["seed"])
            require(
                all(row[k] == v for k, v in measurements.items()),
                "stored episode measurements differ",
            )
            dispatches = [
                e
                for e in events
                if e.trial_id == row["trial_id"] and e.kind == "dispatch_committed"
            ]
            require(len(dispatches) == row["environment_actions"], "action count mismatch")
            success = any(e.payload["terminated"] and e.payload["reward"] == 1 for e in dispatches)
            require(
                success == row["success"] == (row["classification"] == "success_termination"),
                "success disagrees with host dispatch",
            )
            if row["classification"] == "step_limit_truncation":
                require(
                    dispatches[-1].payload["truncated"] and len(dispatches) == row["action_limit"],
                    "step limit mismatch",
                )
        counts = Counter(e.kind for e in events)
        accounting = {"event_counts": dict(counts), "elapsed_seconds": summary["elapsed_seconds"]}
        if name == "haiku-openrouter":
            from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger

            ledger = MemoryCalibrationLedger(Decimal(20), Decimal(0), journal=journal)
            require(ledger.to_dict() == summary["spend"], "spend reconstruction mismatch")
            require(ledger.budget_accounted_spend_usd <= 20, "budget exceeded")
            require(summary["spend"]["in_flight_reservation_usd"] == "0", "in-flight spend remains")
            accounting["spend"] = ledger.to_dict()
        else:
            require(summary["subprocesses_closed"], "CLI subprocess not closed")
            connection = sqlite3.connect(
                f"file:{(directory / 'invocations.sqlite').resolve()}?mode=ro", uri=True
            )
            try:
                cls = (
                    CodexCliInvocationJournal
                    if name.startswith("luna")
                    else ClaudeInvocationJournal
                )
                obj = object.__new__(cls)
                obj._connection = connection
                invocation_integrity = obj.integrity_report()
                require(
                    invocation_integrity == summary["invocation_integrity"],
                    "invocation integrity mismatch",
                )
                statuses = Counter(
                    r[0] for r in connection.execute("SELECT status FROM invocations")
                )
                require(
                    not ({"started", "running", "pending"} & statuses.keys()),
                    "CLI process still pending",
                )
                accounting.update(
                    invocation_statuses=dict(statuses),
                    invocation_integrity_digest=content_digest(invocation_integrity),
                    incremental_experiment_charge_usd=summary["incremental_experiment_charge_usd"],
                    unresolved_invocations=summary["unresolved_invocations"],
                )
            finally:
                connection.close()
    # Explicitly selected publication fields: no provider text, prompts, images, paths or process IDs.
    return {
        "cohort": name,
        "model": summary["model"],
        "adapter_revision": revision,
        "original_plan_digest": content_digest(plan),
        "original_summary_digest": content_digest(summary),
        "benchmark_revision": FROZEN,
        "benchmark_source_hashes": source_hashes,
        "policy_manifests": plan["policy_manifests"],
        "jobs": plan["jobs"],
        "results": results,
        "stop_reason": summary["stop_reason"],
        "assigned": summary["assigned"],
        "recorded": summary["completed"],
        "accounting": accounting,
        "private_audit": {
            "journal_integrity": integrity,
            "result_events_match": True,
            "host_success_and_measurements_match": True,
            "read_only": True,
            "provider_calls_made": 0,
        },
    }


def _journal_context(path):
    from contextlib import closing

    return closing(ReadOnlyJournal(path))


def metrics(rows):
    attempts = [a for r in rows for a in r["first_attempts"]]
    return {
        "episodes": len(rows),
        "successes": sum(r["success"] for r in rows),
        "classifications": dict(Counter(r["classification"] for r in rows)),
        "reached_both_consumers": sum(r["reached_both_consumers"] for r in rows),
        "first_choices_attempted": len(attempts),
        "first_choices_valid": sum(a["valid_choice"] for a in attempts),
        "first_choices_correct": sum(a["correct"] for a in attempts),
        "actions": sum(r["environment_actions"] for r in rows),
        "model_attempts": sum(r["model_attempts"] for r in rows),
    }


def select_chain(snapshots, chain):
    expected = {(j["seed"], j["mode"]): j for j in snapshots[chain[0]]["jobs"]}
    require(len(expected) == 100, "initial panel must contain 100 assignments")
    selected = {}
    interrupted = []
    for name in chain:
        snapshot = snapshots[name]
        jobs = {(j["seed"], j["mode"]): j for j in snapshot["jobs"]}
        require(len(jobs) == len(snapshot["jobs"]), "duplicate cohort assignment")
        require(
            set(jobs) == set(expected) - set(selected),
            "continuation must contain exactly unfinished assignments",
        )
        seen = set()
        for r in snapshot["results"]:
            key = (r["seed"], r["mode"])
            require(key in jobs and key not in seen, "duplicate or unknown episode")
            seen.add(key)
            require(
                all(
                    r[k] == expected[key][k] == jobs[key][k]
                    for k in ("task_id", "task_digest", "action_limit")
                ),
                "assignment identity changed",
            )
            require(
                r["success"] == (r["classification"] == "success_termination"),
                "success/classification mismatch",
            )
            if r["classification"] in TERMINAL:
                selected[key] = {**r, "cohort": name}
            else:
                require(r["classification"] in INTERRUPTED, "unrecognized result classification")
                interrupted.append({**r, "cohort": name})
    require(set(selected) == set(expected), "final panel incomplete")
    return list(selected.values()), interrupted


def paired(rows, seeds=None):
    by_seed = {}
    for r in rows:
        if seeds is None or r["seed"] in seeds:
            by_seed.setdefault(r["seed"], {})[r["mode"]] = r
    require(all(set(p) == {"history", "stateless"} for p in by_seed.values()), "unpaired results")
    outcomes = Counter()
    provenance = Counter()
    for p in by_seed.values():
        h, s = p["history"], p["stateless"]
        outcomes[
            "both"
            if h["success"] and s["success"]
            else "history_only"
            if h["success"]
            else "stateless_only"
            if s["success"]
            else "neither"
        ] += 1
        provenance[h["cohort"] + " / " + s["cohort"]] += 1
    return {
        "pairs": len(by_seed),
        "outcomes": {k: outcomes[k] for k in ("both", "history_only", "stateless_only", "neither")},
        "cohort_pairs": dict(provenance),
    }


def build(directory=DIRECTORY):
    sources = read(directory / "sources.json")
    snapshots = {}
    for name in COHORTS:
        raw = (directory / (name + ".json")).read_bytes()
        require(
            "sha256:" + sha256_bytes(raw) == sources["snapshot_sha256"][name],
            "snapshot digest mismatch",
        )
        snapshots[name] = json.loads(raw)
        require(
            snapshots[name]["cohort"] == name and snapshots[name]["benchmark_revision"] == FROZEN,
            "snapshot identity mismatch",
        )
    representative_source = (
        ROOT / "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json"
    )
    require(
        "sha256:" + sha256_bytes(representative_source.read_bytes())
        == sources["representatives_source_sha256"],
        "representative source changed",
    )
    representatives = read(representative_source)["representative_seeds"]
    require(
        len(representatives) == len(set(representatives)) == 44,
        "representative allocation mismatch",
    )
    models = {}
    for model, chain in CHAINS.items():
        rows, interruptions = select_chain(snapshots, chain)
        models[model] = {
            "conditions": {
                m: metrics([r for r in rows if r["mode"] == m]) for m in ("history", "stateless")
            },
            "paired": paired(rows),
            "representative_pairs": paired(rows, set(representatives)),
            "rows": rows,
            "interrupted_attempts": interruptions,
            "all_attempted_conditions": {
                m: metrics([r for r in rows + interruptions if r["mode"] == m])
                for m in ("history", "stateless")
            },
        }
    require(
        {(r["seed"], r["mode"], r["task_digest"]) for r in models["luna"]["rows"]}
        == {(r["seed"], r["mode"], r["task_digest"]) for r in models["haiku"]["rows"]},
        "model task panels differ",
    )
    output = {
        "schema_version": "pixelgym-pr196-calibration-report-v1",
        "provider_calls_made": 0,
        "benchmark_revision": FROZEN,
        "models": models,
        "cohorts": {
            n: {
                "terminal_conditions": {
                    m: metrics(
                        [
                            r
                            for r in s["results"]
                            if r["mode"] == m and r["classification"] in TERMINAL
                        ]
                    )
                    for m in ("history", "stateless")
                },
                "attempted_conditions": {
                    m: metrics([r for r in s["results"] if r["mode"] == m])
                    for m in ("history", "stateless")
                },
                "adapter_revision": s["adapter_revision"],
                "recorded": s["recorded"],
                "assigned": s["assigned"],
                "stop_reason": s["stop_reason"],
                "accounting": s["accounting"],
            }
            for n, s in snapshots.items()
        },
        "snapshot_sha256": sources["snapshot_sha256"],
    }
    return output, render(output)


def render(data):
    lines = [
        "# PR196 Luna and Haiku calibration",
        "",
        "All 100 final assignments per model have terminal outcomes. These are calibration results, not a confirmatory benchmark, model ranking, or human-gate verdict. The task bank stays frozen at PR196 revision `"
        + FROZEN
        + "`.",
        "",
        "## Final outcomes",
        "",
        "| Model / condition | Episodes | Successes | Step limits | Invalid outputs |",
        "|---|---:|---:|---:|---:|",
    ]
    for model, m in data["models"].items():
        for mode, r in m["conditions"].items():
            lines.append(
                f"| {model} / {mode} | {r['episodes']} | {r['successes']} | {r['classifications'].get('step_limit_truncation', 0)} | {r['classifications'].get('invalid_output', 0)} |"
            )
    lines += [
        "",
        "The final-outcome view retains every normal terminal result, including invalid output. Only missing or infrastructure-interrupted assignments were restarted under explicit owner approval. It is a continuation view, not an intention-to-treat estimate of a single fixed policy. Earlier interrupted attempts remain below and in the snapshots.",
        "",
        "## Paired outcomes",
        "",
        "| Model / subset | Pairs | Both succeed | History only | Stateless only | Neither |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, m in data["models"].items():
        for label, key in [
            ("all seeds", "paired"),
            ("designated representatives", "representative_pairs"),
        ]:
            p = m[key]
            o = p["outcomes"]
            lines.append(
                f"| {model} / {label} | {p['pairs']} | {o['both']} | {o['history_only']} | {o['stateless_only']} | {o['neither']} |"
            )
    lines += [
        "",
        "The fifty seed pairs contain 44 logical clusters. Representatives come from the pre-existing Gemini calibration analysis, not from these outcomes. No significance test, new power analysis, or independent-sample claim is made.",
        "",
        "## Memory observations",
        "",
        "| Model / condition | Reached both consumers | Correct first choices / attempted | Valid first choices | Actions | Model attempts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, m in data["models"].items():
        for mode, r in m["conditions"].items():
            lines.append(
                f"| {model} / {mode} | {r['reached_both_consumers']}/{r['episodes']} | {r['first_choices_correct']}/{r['first_choices_attempted']} | {r['first_choices_valid']} | {r['actions']} | {r['model_attempts']} |"
            )
    lines += [
        "",
        "First-choice denominators count attempted memory choices, including attempts without a valid choice. Episodes that never reach a consumer contribute to the episode denominator but cannot supply a choice observation. These measurements come from stored host checkpoints and dispatches; they are not model self-reports.",
        "",
        "## Cohorts and reliability",
        "",
        "| Cohort | Recorded / assigned | History terminal successes / terminal episodes | Stateless terminal successes / terminal episodes | Elapsed minutes |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, c in data["cohorts"].items():
        h, s = c["terminal_conditions"]["history"], c["terminal_conditions"]["stateless"]
        lines.append(
            f"| [{name}]({name}.json) | {c['recorded']}/{c['assigned']} | {h['successes']}/{h['episodes']} | {s['successes']}/{s['episodes']} | {c['accounting']['elapsed_seconds'] / 60:.1f} |"
        )
    lines += [
        "",
        "Haiku v1 stopped on isolated authentication; v2 stopped on stream parsing. Haiku v3 recorded 100 invalid outputs before the versioned whole-JSON-fence parser repair. Those outcomes are preserved separately and have not been rescored. The owner approved a fresh v4 run; the final Haiku view starts there.",
        "",
        "The Luna continuation adds one retry after a CLI timeout whose process is confirmed stopped. Haiku’s CLI continuation uses the same repair; its last interruption was malformed CLI telemetry. The OpenRouter successor allows one recorded transient-transport retry and keeps unknown charges reserved. Earlier source versions and policy identities remain in each cohort snapshot.",
        "",
        "| Model | Interrupted attempt | Classification |",
        "|---|---|---|",
    ]
    for model, m in data["models"].items():
        for r in m["interrupted_attempts"]:
            lines.append(
                f"| {model} | {r['seed']} / {r['mode']} ({r['cohort']}) | {r['classification']} |"
            )
    lines += [
        "",
        "These interrupted attempts are excluded only from the final replacement view. `results.json` also reports all attempted episodes in the continuation chain, without deleting failures; their repeated seeds are not independent observations.",
        "",
        "## Provider comparability and cost",
        "",
        "Luna uses Codex CLI with medium effort. Haiku’s retained CLI outcomes use Claude Code with default effort; the final 23 assignments use direct Anthropic through OpenRouter with API thinking enabled and no native effort parameter. Different wrappers, prompts added by the CLIs, retry settings, token limits and provider aliases prevent treating this as a controlled model ranking. Haiku’s provider switch was selected for unfinished assignments, not randomized.",
        "",
        "Haiku’s 77 retained CLI terminal outcomes and 23 OpenRouter outcomes remain separately identifiable. Pair provenance, including pairs whose two arms crossed cohorts, is available in `results.json`. The OpenRouter subset is not an independent full-panel replication.",
        "",
    ]
    spend = data["cohorts"]["haiku-openrouter"]["accounting"]["spend"]
    lines += [
        f"OpenRouter reported **USD {spend['spent_usd']}** in confirmed charges, plus **USD {spend['unknown_reservation_usd']}** reserved for {spend['unknown_charge_outcomes']} unknown outcomes: **USD {spend['budget_accounted_spend_usd']}** accounted against the approved USD 20 cap. There are no in-flight reservations. The {spend['wire_requests_sent']} wire requests include the recorded retries. Reservations are conservative budget accounting, not confirmed bills.",
        "",
        "CLI runs recorded zero incremental experiment charges under existing subscriptions. That excludes subscription fees and is not a zero inference-cost claim. CLI cost telemetry is not pooled with API bills. Cohort elapsed times include setup, provider waits and failures, so they are not model-latency comparisons.",
        "",
        "## Historical Gemini context",
        "",
        "The [earlier Gemini report](../grounding-v5-d58-owner-budget-continuation/report.md) and [final-design package](../../plans/grounding-v5-d58-final-design.md) remain unchanged. They retain infrastructure failures in their denominator, unlike this explicitly labelled final-outcome continuation view. Do not pool the cohorts or directly compare their percentages as if they shared execution and failure handling.",
        "",
        "## Provenance, verification and remaining work",
        "",
        "Each response-free snapshot binds the original plan and summary digests, exact policy manifests, adapter revision, assignments, all recorded outcomes, and private audit receipt. Adapter commits are included in this review branch; benchmark generator/backend/history source bytes are checked against PR196. No confirmatory tasks were generated or inspected.",
        "",
        "The private audit opens SQLite read-only, verifies every stored-object digest and the event-chain digest, matches result events to summaries, remeasures exposure and memory choices, checks success against host dispatches, verifies CLI invocation receipts, and reconstructs the OpenRouter ledger. It does not rerun an episode or reinterpret a previously invalid response. It is not a full independent replay of all request construction, parser behavior, or backend evaluation.",
        "",
        "The public verifier checks snapshot hashes, assignment identities, continuation coverage, retained invalid outcomes, model-panel equality, and deterministic report generation. It cannot repeat the private journal audit without the restricted journals. Raw responses, screenshots, checkpoint contents, credentials and operator paths are excluded.",
        "",
        "```sh",
        ".venv/bin/python -m scripts.publish_pr196_calibration --verify",
        "```",
        "",
        "To repeat the private audit, supply `--export /path/to/cohort-directories.json`, a local mapping from the eight cohort IDs above to their original directories. Export requires original adapter commits and journals; verification does not require provider access. The output is deterministic and makes zero model calls.",
        "",
        "Next is the D5.8 owner decision on candidate policy, consistent execution route, sample size and budget. This report does not select them, authorize paid execution, change difficulty, declare a milestone, or complete v5 serving.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    require(not (args.export and args.verify), "export and verify are separate operations")
    if args.export:
        mapping = read(args.export)
        require(set(mapping) == set(COHORTS), "cohort mapping mismatch")
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name in COHORTS:
            snapshot = audit(Path(mapping[name]), name)
            text = encoded(snapshot)
            require(
                "/Users/" not in text and "/private/" not in text and "data:image/" not in text,
                "private surface in export",
            )
            path = DIRECTORY / (name + ".json")
            if path.exists():
                require(path.read_text() == text, "refuse to overwrite changed cohort evidence")
            else:
                path.write_text(text)
            hashes[name] = "sha256:" + sha256_bytes(text.encode())
            print(json.dumps({"audited": name, "episodes": snapshot["recorded"]}), flush=True)
        (DIRECTORY / "sources.json").write_text(
            encoded(
                {
                    "snapshot_sha256": hashes,
                    "benchmark_revision": FROZEN,
                    "representatives_source": "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json",
                    "representatives_source_sha256": "sha256:"
                    + sha256_bytes(
                        (
                            ROOT
                            / "artifacts/grounding-v5-d58-owner-budget-continuation/analysis.json"
                        ).read_bytes()
                    ),
                }
            )
        )
    result, report = build()
    for name, text in {"results.json": encoded(result), "report.md": report}.items():
        path = DIRECTORY / name
        if args.verify:
            require(path.read_text() == text, "generated artifact differs: " + name)
        else:
            path.write_text(text)
    print(
        json.dumps({"verified": True, "models": 2, "final_episodes": 200, "provider_calls_made": 0})
    )


if __name__ == "__main__":
    main()
