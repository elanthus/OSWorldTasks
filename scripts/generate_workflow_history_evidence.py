#!/usr/bin/env python3
"""Generate bounded Git and GitHub workflow-history evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("artifacts/agent-assisted-workflow-history.json")
DEFAULT_REPOSITORY = "elanthus/OSWorldTasks"
DEFAULT_END_REF = "origin/main"
PR_QUERY_LIMIT = 1000
PR_FIELDS = (
    "number,title,url,state,isDraft,headRefName,baseRefName,createdAt,"
    "mergedAt,closedAt,mergeCommit"
)
GIT_FIELD_SEPARATOR = "\x1f"
GIT_RECORD_SEPARATOR = "\x1e"
PR_LABELED_SUBJECT = re.compile(r"\(#(?P<number>[1-9][0-9]*)\)$")


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset: {value!r}")
    return parsed


def parse_git_log(raw: str) -> list[dict[str, Any]]:
    """Parse the delimiter-safe output emitted by the generator's git log query."""

    commits: list[dict[str, Any]] = []
    for record_number, raw_record in enumerate(raw.split(GIT_RECORD_SEPARATOR), start=1):
        record = raw_record.strip("\r\n")
        if not record:
            continue
        fields = record.split(GIT_FIELD_SEPARATOR)
        if len(fields) != 5:
            raise ValueError(
                f"git log record {record_number} has {len(fields)} fields; expected 5"
            )
        sha, authored_at, committed_at, raw_parents, subject = fields
        if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError(f"git log record {record_number} has an invalid SHA: {sha!r}")
        parents = raw_parents.split() if raw_parents else []
        if any(re.fullmatch(r"[0-9a-f]{40}", parent) is None for parent in parents):
            raise ValueError(f"git log record {record_number} has an invalid parent SHA")
        _timestamp(authored_at, f"git log record {record_number} authored_at")
        _timestamp(committed_at, f"git log record {record_number} committed_at")
        commits.append(
            {
                "sha": sha,
                "authored_at": authored_at,
                "committed_at": committed_at,
                "parents": parents,
                "subject": subject,
            }
        )
    if not commits:
        raise ValueError("git log query returned no commits")
    shas = [commit["sha"] for commit in commits]
    if len(set(shas)) != len(shas):
        raise ValueError("git log query returned duplicate commits")
    return commits


def parse_pull_requests(raw: str) -> list[dict[str, Any]]:
    """Parse and validate the selected fields from ``gh pr list``."""

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("gh pr list did not return valid JSON") from exc
    if not isinstance(value, list):
        raise TypeError("gh pr list JSON must be an array")
    required = {
        "number",
        "title",
        "url",
        "state",
        "isDraft",
        "headRefName",
        "baseRefName",
        "createdAt",
        "mergedAt",
        "closedAt",
        "mergeCommit",
    }
    pull_requests: list[dict[str, Any]] = []
    numbers: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError(f"pull request record {index + 1} is missing required fields")
        number = item["number"]
        if type(number) is not int or number < 1:
            raise ValueError(f"pull request record {index + 1} has an invalid number")
        if number in numbers:
            raise ValueError(f"gh pr list returned duplicate pull request #{number}")
        numbers.add(number)
        for key in ("title", "url", "state", "headRefName", "baseRefName", "createdAt"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError(f"pull request #{number} has an invalid {key}")
        if type(item["isDraft"]) is not bool:
            raise ValueError(f"pull request #{number} has an invalid isDraft")
        _timestamp(item["createdAt"], f"pull request #{number} createdAt")
        for key in ("mergedAt", "closedAt"):
            if item[key] is not None:
                if not isinstance(item[key], str):
                    raise ValueError(f"pull request #{number} has an invalid {key}")
                _timestamp(item[key], f"pull request #{number} {key}")
        merge_commit = item["mergeCommit"]
        if merge_commit is not None and (
            not isinstance(merge_commit, dict)
            or re.fullmatch(r"[0-9a-f]{40}", str(merge_commit.get("oid", ""))) is None
        ):
            raise ValueError(f"pull request #{number} has an invalid mergeCommit")
        pull_requests.append(item)
    return pull_requests


def _state_at_cutoff(pull_request: dict[str, Any], cutoff: datetime) -> str:
    merged_at = pull_request["mergedAt"]
    if merged_at is not None and _timestamp(merged_at, "mergedAt") <= cutoff:
        return "MERGED"
    closed_at = pull_request["closedAt"]
    if closed_at is not None and _timestamp(closed_at, "closedAt") <= cutoff:
        return "CLOSED"
    return "OPEN"


def aggregate_history(
    commits: list[dict[str, Any]],
    pull_requests: list[dict[str, Any]],
    *,
    end_sha: str,
) -> dict[str, Any]:
    """Aggregate an inclusive commit/PR window ending at ``end_sha``."""

    commits_by_sha = {commit["sha"]: commit for commit in commits}
    if end_sha not in commits_by_sha:
        raise ValueError(f"end SHA is absent from git log output: {end_sha}")
    roots = [commit for commit in commits if not commit["parents"]]
    if len(roots) != 1:
        raise ValueError(f"expected one root commit, found {len(roots)}")
    start = roots[0]
    end = commits_by_sha[end_sha]
    start_time = _timestamp(start["committed_at"], "start committed_at")
    end_time = _timestamp(end["committed_at"], "end committed_at")
    if end_time < start_time:
        raise ValueError("end commit timestamp precedes the root commit timestamp")

    window_pull_requests = [
        pull_request
        for pull_request in pull_requests
        if start_time
        <= _timestamp(pull_request["createdAt"], "createdAt")
        <= end_time
    ]
    prefix_counts: Counter[str] = Counter()
    base_branch_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    merge_shapes: Counter[str] = Counter()
    merge_shape_pull_requests: defaultdict[str, list[int]] = defaultdict(list)
    merged_pull_request_numbers: list[int] = []
    for pull_request in window_pull_requests:
        head_ref = pull_request["headRefName"]
        prefix = head_ref.split("/", 1)[0] if "/" in head_ref else "(unprefixed)"
        prefix_counts[prefix] += 1
        base_branch_counts[pull_request["baseRefName"]] += 1
        state = _state_at_cutoff(pull_request, end_time)
        state_counts[state] += 1
        if state != "MERGED":
            continue
        merged_pull_request_numbers.append(pull_request["number"])
        merge_commit = pull_request["mergeCommit"]
        if merge_commit is None or merge_commit["oid"] not in commits_by_sha:
            shape = "merge_commit_not_reachable_from_end"
            merge_shapes[shape] += 1
            merge_shape_pull_requests[shape].append(pull_request["number"])
            continue
        commit = commits_by_sha[merge_commit["oid"]]
        if len(commit["parents"]) > 1:
            shape = "multi_parent_merge_commit"
            merge_shapes[shape] += 1
            merge_shape_pull_requests[shape].append(pull_request["number"])
            continue
        subject_match = PR_LABELED_SUBJECT.search(commit["subject"])
        if subject_match and int(subject_match.group("number")) == pull_request["number"]:
            shape = "single_parent_pr_labeled_squash_style"
        else:
            shape = "single_parent_other"
        merge_shapes[shape] += 1
        merge_shape_pull_requests[shape].append(pull_request["number"])

    return {
        "window": {
            "timestamp_basis": "commit committed_at; pull request createdAt",
            "inclusive": True,
            "start": {
                "sha": start["sha"],
                "authored_at": start["authored_at"],
                "committed_at": start["committed_at"],
            },
            "end": {
                "sha": end["sha"],
                "authored_at": end["authored_at"],
                "committed_at": end["committed_at"],
            },
        },
        "commits": {"reachable_inclusive_count": len(commits)},
        "pull_requests": {
            "created_in_window_count": len(window_pull_requests),
            "state_at_cutoff_counts": dict(sorted(state_counts.items())),
            "head_branch_prefix_counts": dict(sorted(prefix_counts.items())),
            "base_branch_counts": dict(sorted(base_branch_counts.items())),
            "agent_assistant_prefix_counts": {
                prefix: prefix_counts[prefix]
                for prefix in ("agent", "codex", "claude")
                if prefix_counts[prefix]
            },
            "merge_commit_shape_counts": dict(sorted(merge_shapes.items())),
            "merge_commit_shape_pull_request_numbers": {
                shape: sorted(numbers)
                for shape, numbers in sorted(merge_shape_pull_requests.items())
            },
            "merged_pull_request_numbers": sorted(merged_pull_request_numbers),
            "not_draft_at_query_time_count": sum(
                not pull_request["isDraft"] for pull_request in window_pull_requests
            ),
            "draft_status_limitation": (
                "isDraft is current query state and does not reconstruct whether a pull "
                "request was originally opened as a draft"
            ),
        },
    }


def _run(command: list[str], repository_root: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def generate(
    repository_root: Path,
    *,
    repository: str,
    end_ref: str,
) -> dict[str, Any]:
    end_sha = _run(
        ["git", "rev-parse", "--verify", f"{end_ref}^{{commit}}"], repository_root
    ).strip()
    git_format = "%H%x1f%aI%x1f%cI%x1f%P%x1f%s%x1e"
    git_command = ["git", "log", "--reverse", f"--format={git_format}", end_sha]
    commits = parse_git_log(_run(git_command, repository_root))
    pr_command = [
        "gh",
        "pr",
        "list",
        "--repo",
        repository,
        "--state",
        "all",
        "--limit",
        str(PR_QUERY_LIMIT),
        "--json",
        PR_FIELDS,
    ]
    pull_requests = parse_pull_requests(_run(pr_command, repository_root))
    if len(pull_requests) >= PR_QUERY_LIMIT:
        raise ValueError(
            f"gh pr list reached its {PR_QUERY_LIMIT}-record limit; refusing an incomplete count"
        )
    evidence = aggregate_history(commits, pull_requests, end_sha=end_sha)
    evidence.update(
        {
            "schema_version": "pixelgym-agent-workflow-history-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "repository": repository,
            "end_ref_resolved": end_ref,
            "source_queries": {
                "git": " ".join(git_command),
                "github": " ".join(pr_command),
            },
        }
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--end-ref", default=DEFAULT_END_REF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    root = args.repository_root.resolve()
    evidence = generate(
        root,
        repository=args.repository,
        end_ref=args.end_ref,
    )
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    window = evidence["window"]
    prs = evidence["pull_requests"]
    print(f"output: {output.relative_to(root)}")
    print(f"start_sha: {window['start']['sha']}")
    print(f"start_committed_at: {window['start']['committed_at']}")
    print(f"end_sha: {window['end']['sha']}")
    print(f"end_committed_at: {window['end']['committed_at']}")
    print(f"reachable_commits: {evidence['commits']['reachable_inclusive_count']}")
    print(f"pull_requests_created: {prs['created_in_window_count']}")
    print(f"branch_prefixes: {json.dumps(prs['head_branch_prefix_counts'], sort_keys=True)}")
    print(f"merge_shapes: {json.dumps(prs['merge_commit_shape_counts'], sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
