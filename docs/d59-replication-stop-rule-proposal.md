# D5.9 replication stop-rule proposal

**Status: unapproved; no execution or re-freeze authorized.** This follow-up prepares
`pixelgym-agent-v5-d59-replication-stop-rule-v2` for an owner decision. The existing
[replication candidate](../artifacts/grounding-v5-d59-replication/execution-plan.json)
remains the PR #256 candidate, with digest
`sha256:bf08e0cbf2a69407e04e4eeed78675e4592d11f714238542bc1520490761444e`.
Its v1 stop rule remains recorded unchanged. The source changes deliberately invalidate
that candidate's source binding; this follow-up cannot run against it. No provider call
is part of this work.

## Prepared amendment and required decisions

The implementation is opt-in through `PROPOSED_STOP_RULE_VERSION` and
`ProposedStopRule` in `pixelgym/grounding/v5/replication_stop_rules.py`.
Constructing options or generating a proposal does not approve execution. The owner must
choose both `failure_streak_scope` and `policy_violation_action`; neither has a default.

| Risk in v1 | Prepared v2 behavior | Owner decision still required |
| --- | --- | --- |
| A subscription rate-limit rejection becomes `request_failure`, which v1 treats as an unexpected classification and stops immediately. | Record the failed assignment in the denominator and continue until a separate consecutive-request-failure limit, proposed as 3, is reached. A reported rate limit schedules and journals backoff before new dispatch: proposed minimum 30 seconds, bounded at 60 seconds, including any usable retry-after hint. | Approve the request-failure disposition, streak limit, and backoff parameters. |
| A refused egress connection, resolved-model mismatch, or credential-shaped output can become `policy_violation`, also unexpected under v1. | Explicitly select `stop`, yielding a named campaign stop, or `continue`, retaining the failed assignment in the denominator. Transport and parser security checks still reject the response. A blocked ledger always takes priority. | Select `policy_violation_action`: `stop` or `continue`. |
| Three overlapping assignments affected by one outage can satisfy the global infrastructure streak of 3. | Apply the unchanged infrastructure limit of 3 under the selected scope, also used for the separate request-failure streak. `per_worker` counts consecutive matching results independently for each worker. `non_overlapping` greedily selects pairwise non-overlapping completed failure intervals: a failure increments only if it starts at or after the last selected interval ends. Overlapping completions neither increment nor extend that end. This does not infer distinct outages; a late bridging interval does not merge previously selected intervals. A nonmatching result resets the relevant streak. | Select `failure_streak_scope`: `per_worker` or `non_overlapping`. |

The backoff delays new dispatch only while the campaign is running and assignments
remain to dispatch; it does not delay draining after a stop. Scheduled and actual wait
are journaled. Already running assignments may finish. Backoff creates no intra-action
rate-limit retry, replays no assignment, and grants no additional cap or runtime
allowance. The existing single counted retry for a confirmed-stopped timeout or
connection reset remains unchanged; internal CLI API retries remain disabled.

## Shared ledger stop remains unchanged

`ClaudeCodeTransport.send` in `pixelgym/grounding/v5/claude_code_policy.py` calls
`retain_unresolved_and_block` for a post-send CLI fault without a parsed envelope, except
an enabled timeout/connection-reset retry whose process is confirmed stopped. The ledger
is shared by all workers. A block by one worker stops new campaign dispatch for every
worker; in-flight assignments are drained and recorded. This remains the registered
`invocation_ledger_blocked` stop under both versions, even when the owner selects a
continuing disposition for request failures or policy violations. The amendment does
not authorize clearing an unresolved invocation or sending again after this block.

## Engineering fixes independent of the amendment

- The attempt-journal writer has an explicit 60-second SQLite busy timeout. A bounded
  writer wait preserves the journal schema and evidence format.
- `CallCapExceededError` stops dispatch as `approved_cap_reached`; in-flight assignments
  still drain. A cap-hit assignment retains an explicit incomplete terminal row and its
  partial journal usage. Cap exhaustion no longer becomes a generic execution error.
- The policy parser binds its expected route to runtime identity, so a response from
  another approved route cannot satisfy this policy's identity check.
- The driver counts started assignments directly instead of scanning worker 0's journal
  for every summary. Integrity summaries use their own read-only connection, and
  `source_revision` is read once from the bound plan without a Git subprocess.

Regression coverage is in `test_grounding_v5_replication_stop_options.py`,
`test_grounding_v5_journal_busy_timeout.py`, `test_grounding_v5_replication_driver.py`,
and the Claude policy tests under `tests/unit/`. These use local fixtures and
synchronization rather than provider calls, OSWorld, network, or wall-clock sleeps.
Executed test counts and runtimes belong in the PR validation record.

## Exact plan changes on a future approved re-freeze

Any successor for these source changes updates `source_binding`, `policy_manifests`,
and `execution_plan_digest`. Selecting v2 additionally changes exactly:

- `stop_rule.version`;
- `stop_rule.episode_outcomes_that_continue` (adds `request_failure`, and
  `policy_violation` only for `continue`);
- `stop_rule.campaign_stops_on` (adds `consecutive_request_failures`, and
  `policy_violation` only for `stop`);
- `stop_rule.amendment_options` (new, containing the two explicit choices, request-failure
  limit, and backoff minimum and maximum);
- `stop_rule.origin`;
- `concurrency.stop_rule_evaluation`.

Tasks, arms, analysis, reliability schedule, action limits, caps, runtime proposal,
retention, and remaining human boundaries do not change. The source and manifest
changes require a fresh versioned freeze even if v1 is retained. After choosing the
protocol, the owner must separately approve the exact successor digest, pilot caps,
and window before any pilot; the full campaign needs its own approval afterward.
The D5.10 verdict and public claims remain human-owned.
