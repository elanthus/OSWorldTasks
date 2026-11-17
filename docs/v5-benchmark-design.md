# v5 agent benchmark design

- **Protocol identifier:** `pixelgym-agent-v5`
- **Status:** v5 benchmark gate (D5.10) declared on 2026-10-25 (UTC) against `fd54dc27` for the environment, platform, and evidence-discipline claim; the Haiku result is descriptive ([decision record](../artifacts/decisions/d5.10.json), [gate record](../artifacts/grounding-v5-d510/human-gate.json))
- **Terms:** see the [glossary](glossary.md) for D-numbers, workflow names, and model codenames

## Goal

The v4c pilot saturated at the episode level: the corrected Qwen run completed 10/10 raw episodes
and 8/10 marked episodes
([stored results](../artifacts/grounding-v4c-pilot-results-qwen3-8-27b-normalized-1000.json)), so
ten short episodes could not separate stronger policies. The same records show deferred-consumer
correctness of 8/10 raw and 7/10 marked, meaning terminal success and first-attempt dependency use
have to be measured separately
([condition records](../artifacts/grounding-v4c-pilot-conditions-qwen3-8-27b-normalized-1000.jsonl)).
An earlier unadapted Qwen score of 0/10 traced to a 1000×1000 coordinate convention
([floor audit](../artifacts/grounding-v4c-pilot-floor-audit-qwen3-8-27b.json)), so v5 fixes
coordinate-adapter identity before any scored call.

v5 measures exact end-to-end task completion by a complete, versioned policy system (model,
prompt, memory, harness, parser, and coordinate adapter) on the same synthetic vendor-onboarding
application. It is a new protocol. It does not rescore Sprint 3, v4, v4b, or v4c, and it does not
make set-of-marks a primary condition.

Out of scope: any change to `PixelGuiEnv`, its observation, actions, key allowlist, reward timing,
evaluator authority, or `info`; non-pixel observations; new agent actions; other applications;
policy training or prompt optimization; and selecting a winner on the confirmatory set.

## Contract

### Environment boundary

The [environment contract](environment-contract.md) is unchanged. v5 adds task generation,
application states, policy adapters, and offline diagnostics around it and relaxes nothing.

### Policy interface

A policy exposes `reset(task_instruction)`, `act(current_screenshot) -> action mapping`, and
`close()`.

- `reset` creates fresh episode-local state. Nothing crosses an episode boundary. Only `act` may
  call a provider.
- `act` receives only the current screenshot. The policy may retain the instruction, earlier
  screenshots, its own actions, and its own notes if its versioned implementation does so. The
  environment supplies no step number, history, checkpoint, partial score, or answer.
- `act` returns one PixelGym action mapping, validated before backend execution. A missing,
  malformed, or invalid action ends the episode unsuccessfully without a second request.
- Provider access is the only outbound network use allowed from the policy sandbox. The
  kernel-enforced launch sandbox and its egress proxy are described in the
  [policy sandbox design](v5-policy-sandbox.md); the D5.9 Haiku pair ran without it.

### Provider-call journal and resume

A runner-owned attempt journal is the only provider-call boundary.

1. `attempt_started` (identity, endpoint, request digest, cap reservation, pre-call state
   checkpoint) is durable before the request is sent.
2. Each attempt has a request deadline, a single cancellation, and a bounded reconciliation
   deadline, and ends in exactly one terminal record. SDK, proxy, and transport auto-retries are
   disabled.
3. The capture-scrubbed canonical response is persisted before any policy logic sees it. A pure
   state reducer derives the post-attempt checkpoint from it.
4. A response is final unless a versioned rule identifies a zero-completion, zero-cost error
   envelope; only that case, or a proven pre-send failure, may be retried within the frozen cap. An
   attempt with an unknown post-send outcome is never retried.
5. A pure parser produces either a sealed failure or a durable `parsed_action_candidate`.
   Validation then seals an unsuccessful result or an action intent.
6. `dispatch-started` is durable before the backend receives the action. `dispatch-committed`
   binds the result and the post-dispatch checkpoint. An uncommitted dispatch is an infrastructure
   failure, never a blind redispatch.

Resume is allowed only from a boundary that cannot duplicate a provider call or environment action,
and only when an `EnvironmentResumeRecord` proves that the backend is in the bound state.

### Policy identity

A policy ID covers the provider and exact snapshot (or disclosed alias), harness and dependency
digests, prompts, reducer, parser, memory policy, sandbox and endpoint allowlist, coordinate
adapter, inference parameters, attempt limits and deadlines, screen and action-schema versions,
cache policy, and code revision. Changing any field creates a new policy ID.

### Task design

Six deterministic workflow families: evidence aggregation, deferred join, conditional precedence,
revision after reveal, visible-error recovery, and review-and-commit. Instructions state the
outcome, not the action sequence. Episodes target 8–14 semantic decisions and 18–40 optimal
low-level actions, with at least two cross-step dependencies. The step limit is
`optimal + max(6, ceil(0.25 * optimal))`.

| Partition | Episodes | Use |
| --- | ---: | --- |
| Development | 24 | Golden paths, mutation fixtures, human inspection |
| Calibration | 60 | Difficulty analysis and generator revision; never a final score |
| Confirmatory | 96 (later enlarged to 192 for D5.9) | Frozen evaluation |

Robustness pairs (`twin_a`, `twin_b`) render the same task semantics through two target-independent
variants and are resampled as one cluster.

### Item admission

An item is admitted only if its spec and annotations are content-bound; the golden policy earns
reward `1.0` exactly once; every declared near-miss and mutation policy earns `0.0`; replays and
resets are semantically and bitwise identical; no answer, target, box, or annotation reaches
policy-visible state; a wrong irreversible commit cannot later succeed; every control is reachable
through the bounded action space; coordinate adapters are tested at the corners and boundaries; and
evidence regenerates without provider calls.

## Analysis plan

- **Primary metric:** exact episode success over all assigned confirmatory episodes. Invalid
  output, request failures, wrong commits, and exhausted budgets stay in the denominator. A missing
  episode makes the run incomplete.
- **Primary test:** a two-sided exact McNemar test on paired outcomes at alpha 0.05, with a
  20-point minimum relevant absolute difference and an 80% power target.
- **Intervals:** Wilson 95% intervals for overall and per-family success, and a family-stratified
  logical-cluster bootstrap (seed 20260911, 10,000 resamples) for the paired difference.
- **Secondary diagnostics,** each with its own denominator: first-attempt critical-decision
  accuracy, dependency retention, recovery success, irreversible-error rate, path overhead, loop
  rate, invalid-output rate, request-failure rate, robustness consistency, termination profile, and
  resource use. No composite score lets cost or latency offset a failed episode.
- **Reliability:** two extra trials per arm on two predesignated episodes per family, reported
  separately and never pooled as hidden retries.
- **Ablation:** a stateless (current-frame-only) reference harness tests whether episode-local
  state contributes to success.

The binding D5.9 pre-registration, copied verbatim from the frozen design, is the
[D5.9 pre-registration record](../artifacts/grounding-v5-d59-freeze/analysis-plan.md).

### Stop rules

- Transport, parse, adapter, invalid-action, unknown-price, or evidence-integrity failure: freeze
  the run and audit without calls before changing difficulty or policy.
- Calibration ceiling, floor, or low discrimination: revise generator-level parameters under a new
  version; never hand-edit items around one model's outputs.
- Confirmatory ceiling, floor, null, or negative result: report it unchanged. No task, threshold,
  exclusion, retry, or policy changes after confirmatory output is observed.
- An incomplete confirmatory run is reported with its failures and missing assignments and is never
  completed by changing the denominator.
- Calls stop at the approved cap. Each provider, model, policy package, phase, and cap needs its own
  human approval, and calibration approval does not cover confirmatory calls.

## Current status

| Deliverable | State | Evidence |
| --- | --- | --- |
| D5.1–D5.5: construct approval, frozen contracts, generator, no-cost admission, stateful harness | Implemented; the calibration runs below used them | [Evidence index](evidence-index.md) |
| D5.6–D5.7: calibration panel and runs | Delivered as descriptive calibration, including negative results | [D5.6 calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md), [supplement](../artifacts/grounding-v5-calibration-supplement/report.md) |
| D5.8: design decisions (difficulty, minimum difference, power, sample, caps) | Decided; Gemini and Haiku analyses retained | [Gemini power check](../artifacts/grounding-v5-d58-final-design/power.md), [Haiku successor analysis](../artifacts/grounding-v5-d58-haiku-successor/report.md) |
| D5.9: confirmatory evaluation | Haiku execution recorded, 432 outcomes. The stop rule was amended after repeated intermittent network failures to allow for retries in the case of intermittent network issues. Confirmatory analysis: [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md). | [D5.9 Haiku report](../artifacts/grounding-v5-d59-haiku-results/report.md), [pre-registration](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
| D5.9 replication: pre-registered successor beside D5.9 | All 432 assignments recorded; frozen analysis and retained-journal audit prepared for owner review. One timeout retains unknown provider completion/usage. The historical and replication differences exceed the frozen disagreement threshold; both are reported. An exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md) from the retained journal is reported separately. D5.10 was declared on 2026-10-25 (row below). | [replication review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), [replication plan](../artifacts/grounding-v5-d59-replication/execution-plan.json), [addendum](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
| D5.10: milestone verdict and public wording | Declared `PASS` against `fd54dc27` on 2026-10-25 (UTC) for the environment, platform, and evidence-discipline claim. The Haiku stateful-versus-stateless result is reported descriptively: history beat stateless in the same direction in all three campaigns (historical 106 vs 15, replication 62 vs 20, post-hoc parser sensitivity 132 vs 20, each of 192 pairs); the magnitude is unresolved and no campaign is selected. Not a pre-registered confirmation, causal parser estimate, OS-enforced isolation claim, or model ranking. Every limitation is classified in the gate record. | [decision record](../artifacts/decisions/d5.10.json), [gate record](../artifacts/grounding-v5-d510/human-gate.json), [fresh validation supplement](../artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REVIEW-NOTE.md), [sensitivity analysis](../artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md) |

Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
a recorded exception that waived OS-level sandbox enforcement, so that evidence cannot support an
OS-enforced isolation claim
([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).

## Amendment (2026-11-15): the stateless chance level, the backend, and replay

This section is appended; nothing above it changed. It records three facts the sections above
omit. The chance-level analysis is post hoc: it was written after all three campaigns' outcomes
were known.

**Each memory task has a stateless chance level.** Every D5.9 task has two three-way consumer
choices (`request_ref` and `verification_ref`). The answer to each appears only on an earlier
screen. The decision screen shows "The source <request|verification> reference is no longer
visible." and three "Match row <ref>" controls. A choice is final and is checked only at
submission, so both must be right. A stateless policy that is otherwise perfect therefore
succeeds 1 time in 9, about 21 of 192 (21.3 expected). Observed stateless successes were 15, 20,
and 20 of 192 in the historical, replication, and parser-sensitivity campaigns. No stateless
success occurred without both first consumer choices correct, in the two campaigns that record
first choices
([chance supplement v1](../artifacts/grounding-v5-stateless-chance-ceiling-v1.md)). Two fixed
rules that use only what the decision screen shows give higher reference levels:

- the best fixed on-screen positions (second control on the request choice, third on the
  verification choice): 26 of 192
  ([v1](../artifacts/grounding-v5-stateless-chance-ceiling-v1.md)); and
- the best pair of fixed rules that may also prefer a reference label by its string order, for
  example the label that sorts first on the request choice and the middle one on the verification
  choice: 30 of 192. This is the maximum over 36 rule pairs, selected on the same 192 episodes,
  so it is an optimistic in-sample level. v1 did not store it; it was added in
  [chance supplement v2](../artifacts/grounding-v5-stateless-chance-ceiling-v2.md), which repeats
  v1 unchanged.

None of these is a proven upper bound for every stateless policy, and only the rules named above
were evaluated. The consequence is that the stateful-versus-stateless comparison was structurally
tilted against the stateless arm: without memory it can only guess or apply a fixed rule to what
the screen shows, while the history arm can carry the answer forward. The three campaigns show that history beat a design-limited
reference arm. They do not isolate the contribution of episode-local state. A control in which the
stateless arm can succeed is [proposed, not run](v5-stateless-control-proposal.md).

**Every model-facing v5 run used the in-process PIL backend.** Every v5 runner in this tree
instantiates `V5FakeBackend` from
[`pixelgym/grounding/v5/backend.py`](../pixelgym/grounding/v5/backend.py) or one of its subclasses,
`MemoryBackend` ([`memory_backend.py`](../pixelgym/grounding/v5/memory_backend.py)) and
`FocusMemoryBackend` ([`memory_focus_backend.py`](../pixelgym/grounding/v5/memory_focus_backend.py)). Every D5.9 policy
manifest records task renderer `pixelgym-v5-memory-focus-backend-v3`; the stored calibration
manifests record that renderer or `pixelgym-agent-v5-task-renderer-v1`. No v5 model call saw a
browser page or the OSWorld backend. The v5 browser app (`pixelgym/grounding/v5/v5_app/`) and its
opt-in browser test (`tests/integration/test_grounding_v5_browser.py`) were not used by any
campaign.

**The recorded actions cannot be replayed in the browser from stored records.** The checked-in
campaign reports (`report.json` in each results folder) store per-episode counts, classifications,
and, for replication and parser sensitivity, first consumer-choice correctness. They do not store
the actions. Each action is in the runner journal: `dispatch_started` names a sealed-intent object
by digest, and the object holds the action. What is missing:

| Campaign | Where the actions are | Status |
| --- | --- | --- |
| historical D5.9 | `attempts.sqlite` of the network-retry execution | Not retained; lost on 2026-09-30 ([evidence index](evidence-index.md)). Cannot be replayed. |
| replication | retained archive, 11 files, 10,672,107,426 bytes, manifest `sha256:99cff640…` | Private: owner's Backblaze B2 bucket and owner-held local copies; not on this checkout's host |
| parser sensitivity | retained archive, 11 files, 15,244,025,902 bytes, manifest `sha256:718167af…` | Same as replication |

A browser replay of the replication and parser-sensitivity episodes needs the owner to restore
those two archives with `scripts.retain_v5_evidence restore` and their recorded digests. A replay
script would then read each trial's committed dispatches in step order and drive the v5 browser
app. No replay was written or run for this amendment.

## Amendment (2026-11-15): which retry rule was in force

This section is appended; nothing above it changed. The provider-call journal rule says an attempt
with an unknown post-send outcome is never retried. That sentence and the pre-registration stay as
written. The executed Haiku plans did not follow it. Each bound a transport retry rule that allows
one counted, same-request retry per action after a confirmed-stopped timeout, a post-send outcome
the runner records as unknown:

| Plan | Transport retry rule | Used by |
| --- | --- | --- |
| [Haiku freeze](../artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json) (pre-registered) | `cli-one-confirmed-stopped-timeout-retry-v1` | the discarded first run only |
| [Counted network retry](../artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json), owner direction 2026-09-23, before the campaign started | `claude-one-stopped-timeout-or-connection-retry-v1`: adds connection resets | historical D5.9 (all 432 assignments) |
| [Replication](../artifacts/grounding-v5-d59-replication/execution-plan.json) and [parser sensitivity](../artifacts/grounding-v5-d59-action-extraction-successor/execution-plan.json), frozen before each campaign | `claude-one-stopped-timeout-connection-or-auth-retry-v1` | replication; parser sensitivity |

The two continuations recorded during the historical campaign
([authorization history](../artifacts/grounding-v5-d59-haiku-results/authorization-history.json),
2026-09-24 and 2026-09-25) changed which failures let the run continue. They did not change the
retry rule. The pre-registration addendum calls those two continuations the stop-rule amendments.
The status table above says the stop rule was amended to allow retries. In the stored records, the
retry permission came from the plan the owner directed on 2026-09-23, before the historical
campaign started (2026-09-24), not from either continuation.

A post hoc supplement counts the retries and shows each arm both ways
([retry sensitivity v1](../artifacts/grounding-v5-retry-sensitivity-v1.md); verify with
`python -m scripts.verify_v5_retry_sensitivity`). Every retried attempt in all three campaigns was
in the history arm. The historical counts are inferred from per-episode counters because its
journal was not retained. The same inference reproduces the recorded retries exactly in the other
two campaigns, but those campaigns contain only timeout retries and no infrastructure failures, so
the check does not exercise the inference's infrastructure-failure branch or a retry after a
connection reset, both of which the historical rule allowed.

| Campaign | Retried attempts (episodes) | History successes as recorded | With retried episodes as failures | Stateless successes |
| --- | ---: | ---: | ---: | ---: |
| historical | 19 (15) | 106/192 | 96/192 | 15/192 |
| replication | 1 (1) | 62/192 | 61/192 | 20/192 |
| parser sensitivity | 5 (5, one in the reliability phase) | 132/192 | 130/192 | 20/192 |

Both columns are reported, and neither replaces the frozen results. Counting a retried episode as a
failure is a pessimistic sensitivity, not evidence that the retry changed its outcome.
