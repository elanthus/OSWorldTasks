from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_workflow_history_evidence import (
    GIT_FIELD_SEPARATOR,
    GIT_RECORD_SEPARATOR,
    aggregate_history,
    generate,
    parse_git_log,
    parse_pull_requests,
)

ROOT_SHA = "1" * 40
MERGE_SHA = "2" * 40
SQUASH_SHA = "3" * 40
END_SHA = "4" * 40


@pytest.fixture
def history_fixture() -> tuple[str, str]:
    def commit(
        sha: str,
        authored_at: str,
        committed_at: str,
        parents: str,
        subject: str,
    ) -> str:
        return GIT_FIELD_SEPARATOR.join(
            (sha, authored_at, committed_at, parents, subject)
        ) + GIT_RECORD_SEPARATOR

    git_log = "\n".join(
        (
            commit(
                ROOT_SHA,
                "2026-08-08T08:22:54+08:00",
                "2026-08-08T08:22:54+08:00",
                "",
                "Initial commit",
            ),
            commit(
                MERGE_SHA,
                "2026-08-09T10:00:00Z",
                "2026-08-09T10:00:00Z",
                f"{ROOT_SHA} {'a' * 40}",
                "Merge pull request #10 from example/agent/change",
            ),
            commit(
                SQUASH_SHA,
                "2026-08-10T10:00:00Z",
                "2026-08-10T10:00:00Z",
                MERGE_SHA,
                "Tighten evidence (#11)",
            ),
            commit(
                END_SHA,
                "2026-08-11T10:00:00Z",
                "2026-08-11T10:00:00Z",
                SQUASH_SHA,
                "Current main head",
            ),
        )
    )
    pull_requests = [
        {
            "number": 8,
            "title": "Inclusive lower boundary",
            "url": "https://github.com/example/project/pull/8",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "boundary",
            "baseRefName": "release",
            "createdAt": "2026-08-08T00:22:54Z",
            "mergedAt": None,
            "closedAt": None,
            "mergeCommit": None,
        },
        {
            "number": 9,
            "title": "Before window",
            "url": "https://github.com/example/project/pull/9",
            "state": "MERGED",
            "isDraft": False,
            "headRefName": "agent/before",
            "baseRefName": "main",
            "createdAt": "2026-08-08T00:22:53Z",
            "mergedAt": "2026-08-08T00:22:53Z",
            "closedAt": "2026-08-08T00:22:53Z",
            "mergeCommit": {"oid": ROOT_SHA},
        },
        {
            "number": 10,
            "title": "Agent branch",
            "url": "https://github.com/example/project/pull/10",
            "state": "MERGED",
            "isDraft": False,
            "headRefName": "agent/change",
            "baseRefName": "main",
            "createdAt": "2026-08-09T09:00:00Z",
            "mergedAt": "2026-08-09T10:00:00Z",
            "closedAt": "2026-08-09T10:00:00Z",
            "mergeCommit": {"oid": MERGE_SHA},
        },
        {
            "number": 11,
            "title": "Codex branch",
            "url": "https://github.com/example/project/pull/11",
            "state": "MERGED",
            "isDraft": False,
            "headRefName": "codex/evidence",
            "baseRefName": "main",
            "createdAt": "2026-08-10T09:00:00Z",
            "mergedAt": "2026-08-10T10:00:00Z",
            "closedAt": "2026-08-10T10:00:00Z",
            "mergeCommit": {"oid": SQUASH_SHA},
        },
        {
            "number": 12,
            "title": "Claude branch",
            "url": "https://github.com/example/project/pull/12",
            "state": "OPEN",
            "isDraft": True,
            "headRefName": "claude/review",
            "baseRefName": "main",
            "createdAt": "2026-08-11T10:00:00Z",
            "mergedAt": None,
            "closedAt": None,
            "mergeCommit": None,
        },
        {
            "number": 13,
            "title": "After window",
            "url": "https://github.com/example/project/pull/13",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "agent/after",
            "baseRefName": "main",
            "createdAt": "2026-08-11T10:00:01Z",
            "mergedAt": None,
            "closedAt": None,
            "mergeCommit": None,
        },
    ]
    return git_log, json.dumps(pull_requests)


def test_parsing_and_aggregation_keep_the_exact_inclusive_window(
    history_fixture: tuple[str, str],
) -> None:
    git_log, pull_request_json = history_fixture

    result = aggregate_history(
        parse_git_log(git_log),
        parse_pull_requests(pull_request_json),
        end_sha=END_SHA,
    )

    assert result["window"]["start"]["sha"] == ROOT_SHA
    assert result["window"]["end"]["sha"] == END_SHA
    assert result["commits"]["reachable_inclusive_count"] == 4
    assert result["pull_requests"]["created_in_window_count"] == 4
    assert result["pull_requests"]["state_at_cutoff_counts"] == {
        "MERGED": 2,
        "OPEN": 2,
    }
    assert result["pull_requests"]["head_branch_prefix_counts"] == {
        "(unprefixed)": 1,
        "agent": 1,
        "claude": 1,
        "codex": 1,
    }
    assert result["pull_requests"]["base_branch_counts"] == {
        "main": 3,
        "release": 1,
    }
    assert result["pull_requests"]["agent_assistant_prefix_counts"] == {
        "agent": 1,
        "codex": 1,
        "claude": 1,
    }
    assert result["pull_requests"]["merge_commit_shape_counts"] == {
        "multi_parent_merge_commit": 1,
        "single_parent_pr_labeled_squash_style": 1,
    }
    assert result["pull_requests"]["merge_commit_shape_pull_request_numbers"] == {
        "multi_parent_merge_commit": [10],
        "single_parent_pr_labeled_squash_style": [11],
    }
    assert result["pull_requests"]["not_draft_at_query_time_count"] == 3


def test_pull_request_parser_rejects_duplicate_records(
    history_fixture: tuple[str, str],
) -> None:
    _, pull_request_json = history_fixture
    pull_requests = json.loads(pull_request_json)
    pull_requests.append(pull_requests[2])

    with pytest.raises(ValueError, match="duplicate pull request #10"):
        parse_pull_requests(json.dumps(pull_requests))


def test_generate_records_requested_ref_and_resolved_sha(
    history_fixture: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    git_log, pull_request_json = history_fixture

    def fake_run(command: list[str], repository_root: Path) -> str:
        assert repository_root == tmp_path
        if command[:2] == ["git", "rev-parse"]:
            return f"{END_SHA}\n"
        if command[:2] == ["git", "log"]:
            return git_log
        if command[:3] == ["gh", "pr", "list"]:
            return pull_request_json
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(
        "scripts.generate_workflow_history_evidence._run",
        fake_run,
    )

    result = generate(
        tmp_path,
        repository="example/project",
        end_ref="frozen-main",
    )

    assert result["end_ref"] == "frozen-main"
    assert result["end_ref_resolved_sha"] == END_SHA
