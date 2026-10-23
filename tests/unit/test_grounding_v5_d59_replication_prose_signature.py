"""Offline checks for the exploratory parse-failure signature diagnostic."""

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import analyze_grounding_v5_d59_replication_prose_signature as prose

PLAN = "sha256:" + "ab" * 32
SECRET_PROSE = "ZEBRA-SENTINEL the field shows T3v but the code is T3V8, which is wrong"


def _journal(path, trials):
    conn = sqlite3.connect(path)
    conn.execute("create table objects (digest text primary key, kind text, data blob)")
    conn.execute(
        "create table events (sequence integer primary key autoincrement, event_key text, "
        "kind text, trial_id text, step_index integer, attempt_index integer, payload blob)"
    )

    def event(kind, trial, step, attempt, payload):
        conn.execute(
            "insert into events (event_key, kind, trial_id, step_index, attempt_index, payload) "
            "values (?,?,?,?,?,?)",
            (
                f"{trial}/{kind}/{step}/{attempt}",
                kind,
                trial,
                step,
                attempt,
                json.dumps(payload).encode(),
            ),
        )

    for trial, spec in trials.items():
        event(
            "d59_replication_assignment_started",
            trial,
            0,
            None,
            {"execution_plan_digest": PLAN, "job": {"seed_record": {"family": spec["family"]}}},
        )
        for step, name in enumerate(spec["events"]):
            event(
                "privileged_dispatch_diagnostic",
                trial,
                step,
                None,
                {"diagnostic": {"event": name, "stage_index": 1}},
            )
        if spec.get("content") is not None:
            step = len(spec["events"])
            digest = f"sha256:{trial}"
            conn.execute(
                "insert into objects values (?,?,?)",
                (
                    digest,
                    "canonical_response",
                    json.dumps({"content": spec["content"], "finish_reason": "stop"}).encode(),
                ),
            )
            event(
                "attempt_completed",
                trial,
                step,
                0,
                {"response_digest": digest, "usage": {"output_tokens": 900}},
            )
            event(
                "sealed_unsuccessful_result",
                trial,
                step,
                0,
                {
                    "failure_code": "parse_failure",
                    "sanitized_reason": spec["reason"],
                    "parser_version": "parser-vX",
                },
            )
    conn.commit()
    conn.close()


def _projection(rows):
    return {
        "execution_plan_digest": PLAN,
        "results": [
            {
                "trial_id": trial,
                "phase": trial.split("-")[2],
                "mode": trial.split("-")[4],
                "classification": cls,
                "model_attempts": attempts,
                "environment_actions": actions,
                "final_stage_index": 1,
            }
            for trial, cls, attempts, actions in rows
        ],
    }


TRIALS = {
    "d59-rep-primary-6001-history-r0": {
        "family": "evidence_aggregation",
        "events": ["correct_transition", "text_input_focused"],
        "content": f'Looking at image 3. {SECRET_PROSE}\n\n```json\n{{"action_type": 2}}\n```',
        "reason": "JSONDecodeError",
    },
    "d59-rep-primary-6003-history-r0": {
        "family": "review_and_commit",
        "events": ["invalid_text_value"],
        "content": '```json\n{"action_type": 2}\n```\n\nI will backspace the lowercase v.',
        "reason": "ValueError",
    },
    "d59-rep-primary-6000-stateless-r0": {
        "family": "evidence_aggregation",
        "events": ["correct_transition"] * 3,
        "content": None,
    },
}
ROWS = [
    ("d59-rep-primary-6001-history-r0", "invalid_output", 3, 2),
    ("d59-rep-primary-6003-history-r0", "invalid_output", 2, 1),
    ("d59-rep-primary-6000-stateless-r0", "success_termination", 3, 3),
]


def test_signature_counts_without_leaking_text(tmp_path):
    journal = tmp_path / "attempts.sqlite"
    _journal(journal, TRIALS)
    with prose.open_journal(journal) as conn:
        result = prose.analyze(conn, _projection(ROWS))
    history = result["by_phase_and_mode"]["primary"]["history"]
    assert history["invalid_output_episodes"] == 2
    assert history["single_rejected_attempt"] == 2
    assert history["response_shape"] == {"prose_then_fence_last": 1, "fence_first_then_prose": 1}
    assert history["failure_reason"] == {
        "parse_failure:JSONDecodeError": 1,
        "parse_failure:ValueError": 1,
    }
    assert history["failures_after_typing_event"] == 2
    assert history["prior_invalid_text_value"] == 1
    assert history["keywords"] == {
        "mentions_code": 1,
        "mentions_case_backspace_or_error": 2,
        "references_history_frames": 1,
    }
    assert result["by_phase_and_mode"]["primary"]["stateless"]["invalid_output_episodes"] == 0
    assert result["typing_diagnostics_primary"]["history"] == {
        "invalid_text_value": 1,
        "text_input_focused": 1,
    }
    published = json.dumps(result) + prose.render(result)
    assert "ZEBRA-SENTINEL" not in published
    assert "T3V8" not in published


@pytest.mark.parametrize(
    "break_projection, message",
    [
        (lambda p: p.update(execution_plan_digest="sha256:" + "cd" * 32), "plan digest"),
        (lambda p: p["results"][0].update(classification="step_limit_truncation"), "disagrees"),
        (lambda p: p["results"].pop(1), "projection lacks"),
    ],
)
def test_projection_binding_failures_raise(tmp_path, break_projection, message):
    journal = tmp_path / "attempts.sqlite"
    _journal(journal, TRIALS)
    projection = _projection(ROWS)
    break_projection(projection)
    with prose.open_journal(journal) as conn, pytest.raises(Exception, match=message):
        prose.analyze(conn, projection)


@pytest.mark.parametrize(
    "content, shape",
    [
        ('{"action_type": 1}', "no_fence"),
        ('```json\n{"action_type": 1}\n```', "fence_only"),
        ('note\n```json\n{"a": 1}\n```\nmore', "fence_inside_prose"),
    ],
)
def test_fence_position(content, shape):
    assert prose.fence_position(content) == shape
