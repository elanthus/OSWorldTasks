# D5.9 replication stop-rule proposal

**Status: protocol choices and a non-executable successor freeze approved on
2026-10-22; execution remains unauthorized.** This follow-up implements
`pixelgym-agent-v5-d59-replication-stop-rule-v2` with the owner-selected options. The existing
[replication candidate](../artifacts/grounding-v5-d59-replication/execution-plan.json)
now binds all production fixes from PRs #256 and #257 at source revision
`dfde3c39a5a37520e61e85eec46fc370dda74ab8`, with digest
`sha256:5c749d996e551b12c2c851628a6c54594a3ab895543c4d696d3f2a0c1234fe99`.
The owner separately authorized this exact replacement freeze with no execution.
The candidate keeps execution disabled and approved model/wire caps at zero.
The earlier v1 candidate remains reproducible in PR #256 history.

## Approved protocol choices and remaining boundaries

The implementation is opt-in through `PROPOSED_STOP_RULE_VERSION` and
`ProposedStopRule` in `pixelgym/grounding/v5/replication_stop_rules.py`.
Constructing options or generating a proposal does not approve execution. On 2026-10-22,
the owner approved continuing request failures with a streak limit of 3, a 30-second
backoff capped at 60 seconds, `policy_violation_action: stop`, and
`failure_streak_scope: per_worker`. The alternative options remain implemented but
unselected. Neither selector has an implicit default.

| Risk in v1 | Prepared v2 behavior | Recorded owner decision (2026-10-22) |
| --- | --- | --- |
| A subscription rate-limit rejection becomes `request_failure`, which v1 treats as an unexpected classification and stops immediately. | Record the failed assignment in the denominator and continue until a separate consecutive-request-failure limit, proposed as 3, is reached. A reported rate limit schedules and journals backoff before new dispatch: proposed minimum 30 seconds, bounded at 60 seconds, including any usable retry-after hint. | Approved: continue failed assignments; limit 3; minimum 30 seconds, maximum 60 seconds. |
| A refused egress connection, resolved-model mismatch, or credential-shaped output can become `policy_violation`, also unexpected under v1. | Explicitly select `stop`, yielding a named campaign stop, or `continue`, retaining the failed assignment in the denominator. Transport and parser security checks still reject the response. A blocked ledger always takes priority. | Selected: `policy_violation_action: stop`. |
| Three overlapping assignments affected by one outage can satisfy the global infrastructure streak of 3. | Apply the unchanged infrastructure limit of 3 under the selected scope, also used for the separate request-failure streak. `per_worker` counts consecutive matching results independently for each worker. `non_overlapping` greedily selects pairwise non-overlapping completed failure intervals: a failure increments only if it starts at or after the last selected interval ends. Overlapping completions neither increment nor extend that end. This does not infer distinct outages; a late bridging interval does not merge previously selected intervals. A nonmatching result resets the relevant streak. | Selected: `failure_streak_scope: per_worker`. |

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

## Exact fields changed by the approved successor freeze

The successor for these source changes updates `source_binding`, `policy_manifests`,
and `execution_plan_digest`. The selected v2 additionally changes exactly:

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
retention, and remaining human boundaries do not change. The owner has approved the
protocol choices and exact replacement freeze, but must separately approve pilot
execution, caps, and window before any pilot; the full campaign needs its own approval
afterward.
The D5.10 verdict and public claims remain human-owned.
