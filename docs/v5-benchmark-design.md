# v5 agent benchmark design

- **Protocol identifier:** `pixelgym-agent-v5`
- **Status:** in progress; no v5 milestone verdict (D5.10) has been declared
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
- Provider access is the only outbound network use allowed from the policy sandbox.

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
| D5.9: confirmatory evaluation | Haiku execution recorded, 432 outcomes. The stop rule was changed after repeated infrastructure failures. Confirmatory analysis not yet produced. | [D5.9 Haiku report](../artifacts/grounding-v5-d59-haiku-results/report.md), [pre-registration](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
| D5.10: milestone verdict and public wording | Not declared; human-owned | — |

Calibration results are descriptive and are not a model ranking. The D5.9 Haiku policies ran under
a recorded exception that waived OS-level sandbox enforcement, so that evidence cannot support an
OS-enforced isolation claim
([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).
