# D5.9 pre-registration record

This file preserves the D5.9 analysis plan beside its evidence after the planning directory was
retired. The sections below are verbatim copies, not paraphrases. Each copy sits in a fenced text
block so that its original relative links are kept byte-for-byte without being rendered from this
directory. A later statistics change must cite this record rather than edit it.

## Source record

- Copied from commit: `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5`. This is the last commit that contains the retired planning
  directory. Both source files are unchanged between their last modifying commit and this commit,
  so the recorded digests are the digests at both.
- Digest method: `git show <commit>:<path> | shasum -a 256`.

| Source path | Copied lines | sha256 of the source file at the commit |
| --- | --- | --- |
| `plans/grounding-v5-d59-confirmatory-freeze.md` | 12–31, 62–75 | `3cd250bb77c908c760a51f231331cb72c165f7efa6eb7ff9655ec89b4d5de292` |
| `plans/grounding-v5-d59-haiku-freeze.md` | 13–23, 63–74 | `1c672e05b87cdb0a5d48e1aeb2dd7c32b3d8643584cbad8f18c365b7114f0ec9` |

## Gemini confirmatory freeze: selected design

Source: `plans/grounding-v5-d59-confirmatory-freeze.md`, lines 12–31. Contains the primary hypothesis, alpha, minimum relevant difference,
target power, stratified logical-cluster bootstrap (seed 20260911, 10,000 resamples, 95%
intervals), and reliability schedule.

```text
## Selected design

- The matched conditions remain Gemini 3.8 Flash screenshot history and the corresponding
  current-frame-only control. Their frozen policy IDs are
  `policy-f3643833caa8b2e9a526` and `policy-1c80069711f0cf37e792`.
- Seeds 6000–6191 supply 192 episodes per arm in six balanced workflow families.
- The allocation contains 168 predesignated independent representatives and 24 robustness pairs.
  Every singleton and each pair's `twin_a` is a primary representative.
- Difficulty bands contain 42 regression canaries, 108 frontier episodes, and 42 ceiling probes.
- The single primary hypothesis remains a two-sided exact McNemar comparison at alpha 0.05, with
  a 20-point minimum relevant absolute difference and 80% target power.
- The family-stratified logical-cluster bootstrap keeps seed 20260911, 10,000 resamples, and 95%
  intervals.
- Reliability uses the first two representatives by ascending seed in each family. Each condition
  receives two additional trials on those twelve tasks; repeats stay outside the primary score.

The memory generator and task schema are versioned as
`pixelgym-agent-v5-generator-memory-v3` and `pixelgym-agent-v5-task-memory-v3`. Version 3 preserves
the v2 task mechanics and adds the second eight-seed-per-family singleton block. Existing D5.8
evidence and its v2 schema remain unchanged and bound to their historical revision.
```

## Gemini confirmatory freeze: stop rules

Source: `plans/grounding-v5-d59-confirmatory-freeze.md`, lines 62–75.

```text
## Remaining human boundary

D5.9 must not start from this PR. Before execution, the owner must inspect the exact plan and
explicitly approve:

1. the exact execution-plan digest;
2. nonzero model-attempt and provider-wire caps no larger than the frozen candidate caps;
3. the proposed 96-hour aggregate runtime window or a replacement window; and
4. the named USD phase caps and the unchanged zero-weight rule for unresolved historical outcomes.

Any generator, policy, route, price, prompt, parser, adapter, cap, reliability subset, or source
change requires a new versioned freeze and admission check. A price change requires a fresh
snapshot and a new plan digest. No post-result tuning or rerun is permitted outside the frozen
reliability schedule.
```

## Haiku freeze successor: frozen comparison

Source: `plans/grounding-v5-d59-haiku-freeze.md`, lines 13–23. The Haiku successor keeps the primary test, alpha, minimum relevant
difference, and target power unchanged, and does not restate the bootstrap definition.

```text
## Frozen comparison

- Model: `claude-haiku-4-5-20251001` through Claude Code CLI 2.1.267 and the `claude.ai` Max
  subscription route.
- Screenshot-history policy: `policy-79441db33362e00a1ac6`.
- Current-frame-only policy: `policy-e1d746cd6a83ffb12a20`.
- Sample: 168 independent representatives and 24 robustness twins, or 192 episodes per arm.
- Primary test: two-sided exact McNemar at alpha 0.05, with the unchanged 20-point minimum
  relevant absolute difference and 80% target power.
- Reliability schedule: two additional trials per arm on the first two independent
  representatives in each workflow family.
```

## Haiku freeze successor: stop rules

Source: `plans/grounding-v5-d59-haiku-freeze.md`, lines 63–74.

```text
## Remaining human boundary

The [execution-plan candidate](../artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json) is
non-executable. Before any confirmatory call, the owner must separately approve:

1. the exact execution-plan digest;
2. nonzero model-attempt and provider-wire caps no larger than the candidate ceilings; and
3. the proposed 168-hour aggregate runtime window or a replacement window.

Any policy, CLI version, provider route, prompt, parser, task, admission, retry, cap, reliability
subset, or security-boundary change requires another versioned successor. The D5.10 verdict and
all public model-quality or security wording remain human-owned.
```

## Addendum (2026-10-22): executed D5.9 successors and the replication protocol

This addendum is appended; nothing above it changed. It closes two gaps found in the D5.10
review: the plan that ran D5.9 was two versioned successors beyond the Haiku freeze quoted above,
and a pre-registered replication now exists beside D5.9. The primary hypothesis, alpha, minimum
relevant difference, target power, and bootstrap above are unchanged for both.

### Source record for the executed D5.9 successors

Copied from commit `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5`, the last commit that contains the retired planning directory, by
`git show <commit>:<path>`. Each copy sits in a fenced text block so its original relative links
are kept byte for byte.

| Source path | Copied lines | sha256 of the source file at the commit |
| --- | --- | --- |
| `plans/grounding-v5-d59-haiku-api-retry-successor.md` | 1–28 | `72cbc3eda9a9ca200547f28987403961c0d742b14643be6e4083cd8f08960449` |
| `plans/grounding-v5-d59-haiku-network-retry.md` | 1–21, 65–71 | `d76e83bed2539866ced8494eb3760d459b4ea80a02c1cb91217977dc70f4f266` |

### Zero-API-retry successor (r2): status and narrow bugfix

Source: `plans/grounding-v5-d59-haiku-api-retry-successor.md`, lines 1–28. This successor bound `CLAUDE_CODE_MAX_RETRIES=0` and new policy IDs
after the first run was discarded on a hidden CLI retry; it was not itself executed.

```text
# D5.9 Haiku zero-API-retry successor

**Status:** response-free successor candidate awaiting a new execution decision. The stopped
campaign is excluded from benchmark scoring and retained only as invalid infrastructure evidence.
No assignment was restarted or replayed, no provider call was made while preparing this successor,
and execution remains disabled.

The stopped campaign exposed a transport-policy defect: Claude Code CLI performed two hidden API
retries inside one runner attempt. The runner correctly stopped when it observed the unauthorized
`system/api_retry` event, but its process-level counters could not count those internal provider
requests. The public, response-free
[discard receipt](../artifacts/grounding-v5-d59-haiku-api-retry-successor/discarded-run.json)
records 18 runner attempts, two CLI retry events, and therefore at least 20 provider API attempts.
It excludes prompts, screenshots, responses, session identifiers, process identifiers, and private
paths.

## Narrow bugfix

The successor sets `CLAUDE_CODE_MAX_RETRIES=0` in the sanitized Claude child-process environment
and binds that value into each policy manifest. This prevents the CLI from initiating an
unbudgeted retry. The parser remains fail-closed: any future `api_retry` event is still an
unauthorized system event and stops the campaign.

Nothing else changes. The model, provider route, prompt, action parser, admitted task manifest,
history reducer, runner-level timeout retry, reliability schedule, statistical comparison,
mechanical caps, runtime proposal, and approved OS-sandbox exception are inherited unchanged from
the predecessor. The successor uses new `d59-haiku-r2-*` trial identifiers and reuses no outcome
from the stopped run.
```

### Counted network-retry successor (r3): the plan that ran

Source: `plans/grounding-v5-d59-haiku-network-retry.md`, lines 1–21. Its execution plan digest is
`sha256:3f50d15f72244cfc8fd422cd16ab8dc6ab8edd873f6c9a92375472671fdcd092`, and its policy IDs
`policy-ebadd1b6c8462c939495` (history) and `policy-392778a54a9f0d242a93` (stateless) are the
ones the D5.9 results bind.

```text
# D5.9 counted network retries

The owner requested on 2026-09-23: “I would like retries to be allowed, these are just intermittent
network issues. Only count errors as failures.” The new transport permits one runner-managed retry
for a confirmed-stopped timeout or connection reset, including Claude's synthetic
`API Error: Connection dropped (ECONNRESET)` response. The two cases share one retry budget per
action. A successful retry continues the episode without classifying it as a failure. A second
transport error remains an infrastructure failure. Authentication errors, malformed model output,
and invalid actions are not retried.

Each send has its own durable attempt reservation and invocation record. The retry uses the same
request and policy checkpoint and dispatches no action until a valid response arrives. Both sends
count against the existing two-attempt limit and aggregate 24,268-attempt / 24,268-wire-request
ceilings. Unknown provider completion remains recorded as unknown. Internal Claude retries remain
disabled so they cannot escape this accounting. No response is discarded to improve a score.

This change creates an r3 protocol candidate. It does not alter either stopped campaign, replace
its recorded failure, or resume its assignments. The prompts, tasks, comparison, reliability
schedule, action limit, and OS-sandbox exception are unchanged. The candidate binds the changed
source files, new manifests, and fresh trial IDs; preparing it makes no provider calls.

```

### Continuations recorded after outcomes were observed

Source: `plans/grounding-v5-d59-haiku-network-retry.md`, lines 65–71. These are the two stop-rule amendments disclosed with the D5.9
analysis; they were not pre-registered.

```text
## Recorded continuations and completed execution

The owner later authorized keeping malformed responses as failed assignments and continuing
with the unrun suffix. On 2026-09-25 the owner extended that rule to exhausted connection resets.
The [authorization history](../artifacts/grounding-v5-d59-haiku-results/authorization-history.json)
records both changes. These amendments were made after observing failures and must be disclosed
with any analysis of the run.
```

### Replication protocol (pre-registered before any call)

The replication is a versioned successor campaign run beside D5.9, frozen in
`artifacts/grounding-v5-d59-replication/execution-plan.json` before any provider call. D5.9 is
not rerun, replayed, or reclassified.

- **Unchanged by digest:** the 192 admitted tasks and their twins, the reliability schedule, the
  action limits, the mechanical caps (24,268 model attempts and wire requests, 12,134
  environment actions), the 168-hour window, and the primary analysis above.
- **Policy pair:** new policy IDs. Each launch runs under the kernel sandbox with the egress
  proxy (`docs/v5-policy-sandbox.md`), so `os_sandbox_applied` is `true`; the route is the
  installed Claude Code CLI 2.1.292 with token authentication (`authMethod: oauth_token`) on
  the Max subscription; CLI API retries remain zero and the counted single transport retry of
  r3 is kept.
- **Stop rule (`pixelgym-agent-v5-d59-replication-stop-rule-v1`):** an episode that ends in
  success, the step limit, invalid output, or an infrastructure failure is recorded and the
  campaign continues. The campaign stops only at an approved cap, the runtime window, a blocked
  ledger, three consecutive infrastructure failures, or an execution error. No assignment is
  replayed and no rule changes after a result is observed.
- **Reporting rule:** the replication is the headline result because its journals are retained;
  D5.9 remains the pre-registered primary and is reported unchanged. If the two paired absolute
  differences disagree by more than the 20-point minimum relevant difference, both results and
  the difference are published as a finding. Neither result is selected after both are seen.
- **Pilot:** four assignments on seed 6000 (both arms, primary and first reliability repeat)
  under their own caps and a 6-hour window, approved separately, never pooled with the full
  campaign, and reported separately.
- **Retention:** the run directory is captured into a retention archive and a verified backup
  before any cleanup, so the secondary diagnostics that D5.9 could not compute are computable.
- **Human boundary:** the owner creates the route login, approves the exact plan digest with
  the pilot caps and window, then approves the full caps and window after the pilot. D5.10 and
  public wording remain human-owned.
