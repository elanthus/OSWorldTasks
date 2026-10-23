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
    "d59-rep-primary-6032-stateless-r0": {
        "family": "deferred_join",
        "events": ["text_input_focused"],
        "content": '```json\n{"action_type": 2, "x": 0, "y": 0, "key": 51, "comment": "P"}\n```',
        "reason": "ValueError",
    },
}
ROWS = [
    ("d59-rep-primary-6001-history-r0", "invalid_output", 3, 2),
    ("d59-rep-primary-6003-history-r0", "invalid_output", 2, 1),
    ("d59-rep-primary-6000-stateless-r0", "success_termination", 3, 3),
    ("d59-rep-primary-6032-stateless-r0", "invalid_output", 2, 1),
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
    assert history["prose_outside_fence"] == 2
    assert history["action_json_parses"] == 2  # fixture fences parse but are partial
    assert history["exact_action_object"] == 0
    stateless = result["by_phase_and_mode"]["primary"]["stateless"]
    assert stateless["invalid_output_episodes"] == 1
    assert stateless["response_shape"] == {"fence_only": 1}
    assert stateless["prose_outside_fence"] == 0
    assert stateless["action_json_parses"] == 1
    assert stateless["exact_action_object"] == 0
    text = prose.reading(result)
    assert "3 episodes ended on a parse failure" in text
    assert "2 of the 3 rejected responses place prose" in text
    assert "1 are a lone fence" in text
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
        ('```json\n{"a": 1}\n```\n```json\n{"a": 2}\n```', "multiple_fences"),
    ],
)
def test_fence_position(content, shape):
    assert prose.fence_position(content) == shape


@pytest.mark.parametrize(
    "content, parses, exact",
    [
        ('{"action_type": 1, "x": 5, "y": 6, "key": 0}', True, True),
        ('```json\n{"action_type": 1, "x": 5, "y": 6, "key": 0}\n```', True, True),
        ('```json\n{"action_type": 1, "x": 5, "y": 6, "key": 0, "note": "x"}\n```', True, False),
        ('```json\n{"action_type": 1.0, "x": 5, "y": 6, "key": 0}\n```', True, False),
        ("prose\n```json\n{\n```", False, False),
    ],
)
def test_action_body(content, parses, exact):
    assert prose.action_body(content) == {"json_parses": parses, "exact_action_object": exact}


def test_unexpected_phase_token_is_rejected():
    with pytest.raises(Exception, match="unexpected trial id"):
        prose.split_trial("d59-rep-garbage-6000-history-r0")
