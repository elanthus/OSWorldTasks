# PixelGym v5 Agent Benchmark — Stateful End-to-End GUI Policy Evaluation

**Status:** design only — this document authorizes neither implementation nor model calls

**Primary reader:** the project owner deciding whether to approve implementation, calibration,
provider spend, and a later confirmatory evaluation

**Proposed protocol identifier:** `pixelgym-agent-v5`

## Outcome

Build a deterministic benchmark that can distinguish stronger end-to-end GUI agents after the v4c
pilot saturated at the episode level. V5 evaluates a complete versioned policy system — model,
prompt, memory, harness, parser, and coordinate adapter — while preserving PixelGym's existing
pixel-only observation, bounded action, privileged evaluator, sparse reward, and determinism
contracts.

V5 is a new protocol, not a reinterpretation of any frozen grounding result. It keeps the synthetic
vendor-onboarding application and increases discrimination through compositional workflows,
stateful policy behavior, controlled recovery, held-out parameter combinations, and a larger
confirmatory set. It does not make a public performance claim or declare any existing milestone
gate passed.

Implementation starts only after a separate human scope decision. Paid calibration and
confirmatory calls each require their own later approvals.

## Routing basis

The corrected Qwen v4c run completed 10/10 raw episodes and 8/10 marked episodes
([stored results](../artifacts/grounding-v4c-pilot-results-qwen3-8-27b-normalized-1000.json)).
The preregistered v4c rule routes either condition at 9–10/10 to
`report_saturation_stop`; any successor requires a new human scope decision
([v4c plan](grounding-v4c-longer-horizon-pilot.md#7-metrics-and-preregistered-routing)).

The episode totals do not imply flawless state use. The stored
[v4c condition records](../artifacts/grounding-v4c-pilot-conditions-qwen3-8-27b-normalized-1000.jsonl)
report deferred consumer correctness of 8/10 raw and 7/10 marked, while recovery allowed three of
those incorrect first attempts to end in success. The run therefore supports two design
conclusions:

1. Ten six-to-eight-decision episodes cannot distinguish a policy that reaches the raw ceiling.
2. Terminal success and first-attempt dependency use must remain separate measurements.

The earlier unadapted Qwen score remains frozen at 0/10 in both conditions. Its no-call floor audit
identified a 1000×1000 coordinate convention rather than a native 1024×768 convention
([floor audit](../artifacts/grounding-v4c-pilot-floor-audit-qwen3-8-27b.json)). V5 must therefore
freeze and test coordinate-adapter identity before any scored call.

These ten-episode results are calibration evidence for this design decision, not a general claim
about Qwen, set-of-marks, or GUI-agent performance.

## Ownership legend

- **YOU** — Approve the v5 construct and scope, human-review protocol, provider and policy panel,
  paid-call caps, calibration routing, final generator and seed freeze, confirmatory run,
  benchmark verdict, and public claims.
- **AGENT · medium** — Implement schemas, deterministic fixtures, agent adapters, reports, and
  bounded tests after their interfaces are frozen.
- **AGENT · high** — Implement stateful evaluation semantics, evaluator boundaries, generator
  determinism, evidence lineage, statistical analysis, call-cap enforcement, and failure routing.
- **PAIR** — Inspect task samples, calibration evidence, difficulty changes, and final claims.

Only one agent writes shared environment, generator, schema, or evaluation paths at a time.

## Goals

V5 should:

- Measure exact end-to-end task completion by a policy that may retain private state within one
  episode.
- Exercise planning, evidence aggregation, deferred dependencies, revision, visible-error
  recovery, and safe final commitment in one deterministic application.
- Produce items of varied difficulty, including a large frontier band that yields mixed outcomes
  among the approved calibration policies.
- Separate terminal success from critical-decision, recovery, efficiency, robustness, cost, and
  latency diagnostics.
- Compare compatible policies on identical task seeds with paired uncertainty estimates.
- Generate every reported result from immutable per-step and per-episode evidence.
- Preserve the existing PixelGym environment and reward invariants without exposing new agent
  actions or privileged observations.

## Non-goals

V5 does not:

- Replace or rescore the frozen Sprint 3, v4, v4b, or v4c experiments.
- Change `PixelGuiEnv`, its observation space, the four-field action mapping, key allowlist, reward
  timing, evaluator authority, or `info` contents.
- Add DOM, accessibility-tree, OCR-service, bounding-box, step-index, action-history, or
  evaluator-diagnostic observations. A policy may process screenshots it legitimately received as
  part of its private implementation.
- Add arbitrary Python, shell, browser-navigation, tool-use, `DONE`, macro, or multi-action
  commands to the environment.
- Generalize beyond the synthetic vendor-onboarding application or claim broad real-world GUI
  competence.
- Add spreadsheets, file upload, extra applications, multi-resolution evaluation, or other
  stretch work.
- Make set-of-marks a primary v5 condition. The frozen grounding experiments retain that research
  question; a future end-to-end marks ablation needs a separate protocol.
- Train or tune a policy, automatically optimize prompts, or select a winner on the confirmatory
  set.
- Authorize paid calls, cloud spend, deployment, or public benchmark wording.

## Benchmark contract

### Policy interface

The evaluation harness exposes a conceptual policy interface:

```text
reset(task_instruction) -> None
act(current_screenshot) -> {action_type, x, y, key}
close() -> None
```

The interface is conceptual until D5.2 freezes its Python protocol and schemas. Its behavior is
already bounded:

- `reset` creates fresh episode-local policy state. No state, provider conversation, response
  cache entry that contains task-specific conclusions, or self-authored memory may cross an
  episode boundary. `reset` and `close` make no provider requests; only `act` may call a provider.
- `act` receives the current screenshot. The policy retains the task instruction, previous
  screenshots, its own actions, provider messages, and self-authored memory only if its versioned
  implementation chooses to retain them.
- The environment supplies no action history, step number, checkpoint, partial score, mismatch,
  expected answer, target box, or transition label.
- `act` returns exactly one existing PixelGym action mapping. `PixelGuiEnv` validates the mapping
  before backend execution. A missing, malformed, or invalid action ends the episode as
  unsuccessful; the harness does not ask again.
- A policy may make zero, one, or multiple provider requests while producing one environment
  action only when that behavior and its internal call cap are frozen in the policy package.
  Every request and response remains attributable. Environment-action count and provider-call
  count are reported separately.
- Provider access is the only outbound network use permitted from the policy execution sandbox.
  The runner and backend execute outside that sandbox and retain only the application-launch,
  screenshot, input, and controller channels required by the existing backend protocol; this
  required environment traffic is not policy egress and is never exposed as a policy capability.
  The sandbox allowlists the configured provider endpoint for a paid policy, or no endpoint for a
  no-cost fake policy. External search, arbitrary URLs, browser or DOM inspection, shell execution,
  knowledge tools, inbound listeners, shared storage, and direct or indirect communication with
  another policy are denied.
- A response-producing provider call is final. A pre-send transport failure, or a failure for which
  the provider's idempotency or reconciliation API proves that no response was produced, may be
  retried only under a predeclared, versioned rule. An attempt with an unknown post-send outcome is
  not retried. Every request attempt remains in the attempt journal and counts toward
  `max_provider_calls_per_action`.

D5.2 must freeze a runner-owned, injected attempt journal before implementing a provider-backed
policy. The journal is not a policy observation or tool. It is the only provider-call boundary and
uses deterministic trial, step, and attempt identities. Before sending a request, it durably writes
`attempt_started` with that identity, provider-endpoint identity, request digest, frozen
idempotency key or reconciliation mode, call-cap reservation, and an access-controlled
reconstructable pre-call policy-state checkpoint with its digest. The provider request may start
only after this record is durable.

After receiving a response, the provider adapter excludes credentials and normalizes the allowed
provider fields into one versioned, capture-scrubbed canonical response record. The journal persists
those exact bytes and their digest before exposing them to policy logic, the parser, or another
attempt. Unscrubbed provider bytes are never an artifact or log input. A frozen pure state reducer
then derives a reconstructable post-attempt policy-state checkpoint from the pre-call checkpoint and
the canonical response bytes. `attempt_completed` atomically links the canonical response digest,
usage, completion status, and post-attempt checkpoint digest; a terminal transport-failure record
links the deterministic failure-state checkpoint instead. The next attempt's pre-call checkpoint
must equal the preceding terminal attempt's post-attempt checkpoint.

If execution stops after canonical response persistence but before `attempt_completed`, recovery
reruns only the frozen state reducer against the same bytes and must reproduce the checkpoint
bitwise. On resume, an `attempt_started` record without a canonical response or terminal record may
be reconciled only through the frozen provider mechanism under the same attempt identity. If the
exact outcome cannot be recovered, the episode receives an infrastructure failure; the runner does
not issue another request or silently reinterpret the attempt.

The frozen parser is a pure deterministic function of the exact canonical response bytes referenced
by the completed attempt records, parser version, and latest post-attempt policy-state checkpoint.
It never reads an unscrubbed response or a publishable derivative. Uninterrupted execution invokes
it once. If the process stops before a parse outcome is durable, recovery may evaluate that same
function again; tests must prove byte-identical output, and this is not a provider retry. A parse
failure atomically seals an unsuccessful action result containing the canonical-attempt references,
parser version, sanitized failure code and reason, and policy-state checkpoint. It dispatches no
environment action, performs no provider retry, and retains that result as report input.

On parse success, the runner atomically writes a `parsed_action_candidate` record containing the
candidate, exact attempt identities, parser version, and reconstructable post-parse action-boundary
policy-state checkpoint with its digest. Validation reads only that durable record. Invalid
candidates produce a sealed unsuccessful result with the action-schema version and validation
reason. Valid candidates produce one sealed action intent linked to the candidate record. If
recovery finds a candidate record with neither result nor intent, it reruns only deterministic
validation and seals the appropriate outcome; it never reruns the parser or dispatches before the
intent is durable. The post-parse checkpoint, rather than the pre-call checkpoint, is restored
before resuming a sealed intent or beginning the next `act`.

The journal then records dispatch-started and dispatch-committed states, with the latter binding the
action intent to the resulting screenshot, reward, and termination state. Dispatch commit also
stores a deterministic post-dispatch policy-state checkpoint derived from the post-parse checkpoint,
committed action, and result; that checkpoint becomes the state before the next screenshot is
supplied to `act`. Resume may reuse a stored response without another provider request and may
dispatch an intent only when no dispatch-started record exists. An uncommitted dispatch-started
record is an infrastructure failure unless the backend supplies a separately proven idempotent
transaction; it is never blindly redispatched. Policy implementations may wrap the journal behind
their conceptual `act` interface, but may not replace it with private, non-durable bookkeeping.

A sealed intent is a pre-dispatch resume boundary only when it binds a versioned
`EnvironmentResumeRecord` for the exact current backend state. D5.2 must freeze a runner-facing v5
backend extension that provides either content-addressed checkpoint/restore or a stable session
identity with a proven liveness and reconnect guarantee. The resume record binds task identity,
backend/session identity, step count, current screenshot digest, privileged application-state
digest, and the selected restore or reconnect mechanism. Recovery must restore or reconnect and
verify every binding before writing dispatch-started. If the backend cannot prove the same state,
the episode ends as an infrastructure failure without dispatch. This extension adds no policy
observation or action and does not change `PixelGuiEnv` reward or termination semantics.

The benchmark evaluates a policy system, not an unnamed base model. Results must use the complete
policy identity.

### Policy identity

Before the first task, the harness resolves an immutable policy manifest containing:

- policy ID and schema version;
- model provider, exact snapshot when available, and disclosed alias otherwise;
- agent-harness source digest and dependency lock digest;
- system prompt, task-prompt renderer, provider-response capture schema, state reducer, parser, and
  memory-policy versions;
- coordinate-adapter name, source digest, input convention, and output convention;
- inference parameters, context limits, internal provider-call limit, and transport-retry rule;
- screenshot dimensions, action-schema version, and key-allowlist version;
- cross-episode cache policy, which defaults to disabled; and
- code revision and dirty-worktree policy.

Changing any field creates a different policy ID. A provider alias that cannot pin immutable model
weights remains a disclosed limitation and may not be compared as if it were an exact snapshot
without a warning.

### Environment boundary

The current core contract remains authoritative:

- Observation is one RGB `uint8` screenshot matching the backend dimensions.
- Actions are `NOOP`, bounded `CLICK`, or versioned allowlisted `KEY` mappings.
- Invalid actions are rejected before backend execution and are never clipped or coerced.
- Reward stays `0.0` until an exact privileged submission, then becomes `1.0` exactly once.
- Success sets `terminated=True`; the step limit sets `truncated=True`; stepping after either
  raises.
- Agent-facing `info` remains limited to `task_id`.

V5 implementation may add versioned task generation, application states, policy adapters, and
offline diagnostics around this boundary. It must not relax the boundary itself.

## Task design

### Workflow families

V5 uses six deterministic workflow families. Every task instruction states the desired outcome,
not the correct action sequence.

| Family | Required behavior | Intended failure signal |
|---|---|---|
| Evidence aggregation | Inspect multiple request and evidence panels, then combine two or three facts before acting | Choice based on partial evidence |
| Deferred join | Preserve two references across intervening screens and use their conjunction at later consumers | Lost, swapped, or incompletely used state |
| Conditional precedence | Apply a visible base rule, exception, and precedence rule to the same case | Shallow rule matching or exception neglect |
| Revision after reveal | Make a reversible provisional choice, reveal new evidence, then revise the affected choice before continuing | Plan rigidity or failure to reconcile |
| Visible-error recovery | Submit or advance into a deterministic visible error state, diagnose it, and repair only the implicated fields | Repetition, broad reset, or wrong repair |
| Review and commit | Verify a summary containing plausible distractors, correct any remaining mismatch, and perform one final irreversible commit | Premature commit or unsafe confirmation |

The application may reveal new evidence only in response to an agent action. It may not use clocks,
network calls, random asynchronous changes, animation, or hidden state that a screenshot-only policy
cannot resolve.

### Difficulty knobs

The generator varies capability-relevant factors rather than relying on small hit targets:

- semantic-decision count and low-level action horizon;
- number, source, and separation of deferred facts;
- dependency fan-in at a later decision;
- number of provisional choices that later evidence confirms or invalidates;
- base-rule, exception, and precedence depth;
- semantic similarity of visible distractors;
- visible recovery-branch depth;
- mix of click, focus movement, short text entry, correction, and confirmation actions; and
- target-independent wording, option order, and fixed-screen layout variants.

Each episode targets 8–14 semantic decisions and an optimal trajectory of 18–40 low-level
environment actions. Required typed values use short deterministic strings so character entry does
not dominate the construct: no individual required value exceeds five printable characters and no
golden trajectory types more than twelve printable characters in total.

Each episode contains at least two cross-step dependencies. At least one dependency spans four
intervening semantic decisions. The correct golden path remains solvable from the instruction,
screenshots received so far, and the policy's own episode-local state.

The episode step limit is frozen per task as:

```text
optimal_low_level_actions + max(6, ceil(0.25 * optimal_low_level_actions))
```

The slack supports observable correction without turning repeated guessing into an unlimited
strategy. The manifest stores both terms; display rounding is not used in the calculation.

### Dataset partitions

The design targets three disjoint partitions:

| Partition | Episodes | Allocation | Purpose |
|---|---:|---|---|
| Development | 24 | 4 per workflow family | Public implementation, golden-path, mutation, and reviewer inspection fixtures |
| Calibration | 60 | 10 per workflow family | Difficulty analysis and generator-level revision; never a final benchmark score |
| Confirmatory | 96 | 16 per workflow family | Frozen evaluation after the final generator, policy, thresholds, and call caps are approved |

The calibration set contains twelve logical robustness pairs, two per family. Those pairs produce
24 episodes; the other 36 calibration episodes are unpaired. This allocation lets calibration
measure robustness-pair consistency before the final generator is frozen.

The confirmatory set contains 24 logical robustness pairs, four per family. Each pair renders the
same task semantics through two target-independent wording/order/layout variants, producing 48
episodes. The other 48 episodes are unpaired. Statistical resampling treats the two variants as one
logical cluster where appropriate.

D5.2 freezes explicit, disjoint seed lists and canonical parameter records before capture or model
calls. Calibration may change generator-level difficulty distributions, but doing so creates a new
generator version. Confirmatory tasks are generated only from the final approved version and are
never edited in response to policy output.

The target composition is approximately 20% regression canaries, 60% frontier items, and 20%
ceiling probes across the complete set, with every workflow family represented in each band. These
are calibration targets, not post hoc labels forced onto observed results.

## Calibration and item admission

### No-cost validation panel

Before any paid call, every task is exercised by:

- the scripted golden policy through the normal `PixelGuiEnv` boundary;
- a target-independent random-action floor with a frozen seed;
- a memoryless mutation policy that follows locally correct controls but cannot retain deferred
  facts;
- mutation policies for skipped revision, repeated invalid repair, premature commit, stale task
  submission, and step-budget exhaustion; and
- deterministic replay of each recorded action trace.

Synthetic policies validate contracts and intended failure surfaces. Their results are not evidence
of real-model quality.

### Human usability review

Before provider calibration, the owner reviews a stratified development sample for legibility,
instruction sufficiency, screenshot-only solvability, action budget, and absence of target leakage.
No human-performance percentage may be claimed without a separately preregistered human study.

### Paid calibration panel

Provider calibration requires a new human-approved policy panel and exact aggregate call cap. The
panel should contain at least four meaningfully distinct policy systems spanning the expected
ability range. At least one approved model should run in both stateful and stateless reference
harnesses so the calibration can test whether the task bank actually exercises episode-local state.

The calibration report includes, for each item and family:

- policy success frequency;
- success separation between the stronger and weaker halves of the panel;
- first-attempt critical-decision and dependency-retention outcomes;
- action-budget and failure-route distributions;
- robustness-pair consistency; and
- request failure, invalid output, cost, and latency evidence.

Item Response Theory may be exploratory when the response matrix contains enough distinct policies
for stable estimation. It does not become an admission gate merely because software can fit a
model. With a small panel, success frequency, paired outcomes, and bootstrap stability remain the
authoritative diagnostics.

### Admission rules

An item enters a frozen partition only when:

1. Its canonical spec, generator version, task ID, initial screenshot, instruction, action limit,
   expected terminal state, and critical-decision annotations are content-bound.
2. The golden policy reaches reward `1.0` exactly once through the normal environment boundary.
3. Every declared near-miss and mutation policy receives reward `0.0` and the expected terminal or
   truncation classification.
4. Replaying every declared golden, recovery, near-miss, and robustness trace twice produces the
   same semantic state and bitwise-identical screenshot at each corresponding step.
5. Resetting the task twice with the same seed clears prior submissions and reproduces the same
   task JSON, task ID, initial state, and screenshot.
6. Expected answers, target controls, boxes, critical-decision labels, and transition annotations
   are absent from policy-visible observations, prompts, `info`, logs returned to the policy, and
   response caches.
7. Wrong irreversible commits cannot later transition to success.
8. Every actionable control needed by a valid trace is reachable through the existing bounded
   action space and versioned key allowlist.
9. Coordinate-adapter tests cover screen corners, boundary rejection, declared normalized grids,
   and the exact screen dimensions before a live scored call.
10. Evidence generation is reproducible without provider calls.

V5 does not require exhaustive image capture for every possible typed prefix or arbitrary wrong
path. It requires complete deterministic replay coverage for declared valid, recovery, near-miss,
robustness, and mutation traces, plus property tests over generated specs and action boundaries.

## Evaluation execution

For each policy and task, the runner:

1. Resolves and verifies the immutable task, generator, policy, environment, price, and call-cap
   manifests.
2. Resets the environment and policy independently. Cross-episode policy state is empty.
3. Stores the initial screenshot and its digest before the first policy action.
4. Calls the policy with the current screenshot and the runner-owned attempt journal injected into
   its provider-call boundary.
5. Writes `attempt_started` before each request, persists the capture-scrubbed canonical response
   bytes before policy consumption, and writes exactly one completed, failed, or reconciled terminal
   attempt record with its post-attempt state before parsing or starting another attempt.
6. Runs the pure parser and atomically persists either a sealed failure or a
   `parsed_action_candidate` containing the post-parse checkpoint.
7. Validates the durable candidate through the existing PixelGym action contract. An invalid
   candidate seals an unsuccessful result; a valid candidate binds the current
   `EnvironmentResumeRecord`, seals the action intent, and forms a supported pre-dispatch resume
   boundary only when backend restore or reconnect has been proven. Recovery from an unconsumed
   candidate repeats only this deterministic validation step.
8. On recovery, restores or reconnects and verifies the bound environment state. It then writes
   dispatch-started immediately before dispatching the valid action once and writes
   dispatch-committed with the resulting screenshot, privileged host-side diagnostic event, and
   post-dispatch policy-state checkpoint.
9. Seals the per-step trace, final submission evidence, usage, cost, latency, and policy-local
   metadata under content hashes.
10. Aggregates results only from the sealed records. Report generation never reruns the policy or
   reinterprets missing evidence.

An evaluation may resume after an infrastructure interruption only from a boundary that cannot
duplicate a provider call or environment action. Resume semantics, attempt identity, and policy
state reconstruction must be proven before a paid run. A failed reconstruction ends the task as an
infrastructure failure; it does not silently restart the episode under the same trial identity.

## Metrics

### Primary metric

The primary metric is exact episode success on all confirmatory episodes:

```text
successful exact terminal submissions / all assigned episodes
```

Invalid actions, unparseable actions, request failures, wrong commits, and exhausted step budgets
remain in the denominator. A missing episode is not silently discarded and makes the run
incomplete.

### Secondary diagnostics

| Diagnostic | Definition |
|---|---|
| First-attempt critical-decision accuracy | Fraction of declared critical decisions whose first dispatched action after state entry takes the correct transition; invalid and non-progressing actions are incorrect |
| Dependency retention | Fraction of declared deferred consumers resolved correctly before entering a recovery state |
| Recovery success | Fraction of entered visible recovery states that later reach exact terminal success within the frozen budget |
| Irreversible-error rate | Fraction of episodes that execute a wrong final or otherwise irreversible synthetic commit |
| Path overhead | Actual environment actions minus golden optimal actions, reported separately for successful and unsuccessful episodes |
| Loop rate | Fraction of episodes containing a repeated observation/action cycle under the frozen loop definition |
| Invalid-output rate | Fraction of policy decisions that fail parsing or canonical action validation |
| Request-failure rate | Failed provider requests divided by all attempted provider requests, with retry attempts retained |
| Robustness consistency | Fraction of logical robustness pairs with identical binary success outcomes, plus the signed variant delta |
| Termination profile | Counts of success termination, step-limit truncation, invalid output, request failure, and infrastructure failure |
| Resource use | Provider calls, environment actions, prompt/completion tokens, attributed cost, and p50/p95/max latency |

Critical-decision and recovery annotations are privileged offline diagnostics. They never affect
reward or cross into policy-visible state.

No scalar composite permits lower cost, lower latency, or better diagnostics to compensate for a
failed episode. Promotion or comparison gates remain conjunctive under a separately frozen policy.

## Statistical analysis

The confirmatory report must:

- show the numerator, denominator, point estimate, and 95% Wilson interval for overall and
  per-family episode success;
- compare compatible policies on identical tasks using the paired success table and a two-sided
  exact McNemar test;
- report the paired absolute success difference with a fixed-seed bootstrap confidence interval;
- resample logical robustness pairs as clusters rather than treating their variants as independent;
- report every secondary diagnostic with its eligible denominator and exclusions;
- keep calibration, confirmatory, and repeated-trial observations separate; and
- identify analyses not frozen before the run as exploratory.

D5.8 uses calibration discordance rates to perform a paired power calculation for the smallest
policy difference the owner considers decision-relevant. The target confirmatory size is 96
episodes. If it cannot meet the approved sensitivity and power target, work stops for a human
choice to enlarge the frozen design or accept the limitation; the runner does not silently alter
the sample.

A reliability subset contains twelve predeclared confirmatory episodes, two per workflow family.
Each policy may run two additional independent trials on that subset under a separate approved call
cap. The first designated trial remains part of the primary score; repeats estimate provider and
policy variability and are never pooled as hidden retries.

## Evaluation conditions and ablations

The confirmatory primary condition uses raw 1024×768 screenshots and the stateful policy package.

Two diagnostics are permitted when frozen and separately budgeted:

1. A stateless reference harness on a 24-episode stratified subset, used to test whether stateful
   behavior contributes to success.
2. The twelve-episode repeated-trial subset described in the statistical plan.

Set-of-marks is outside the initial v5 protocol. Dynamic text entry and policy-dependent paths make
a complete target-independent overlay bank a separate design problem, and v4c did not establish a
benefit for the corrected Qwen policy. A future marks condition must freeze its own candidate
generation, dynamic-state coverage, leakage audit, call cap, and routing before use.

## Evidence contract

The v5 run must preserve and content-bind:

- generator source, schema, parameters, seed lists, canonical task specs, and partition manifest;
- initial screenshots and every screenshot observed in evaluated traces;
- golden, recovery, near-miss, robustness, and mutation traces used for admission;
- policy manifest, source/package digest, prompts, parser, memory policy, adapter, and parameters;
- provider attempt-started, canonical-response, and terminal-record journal; pre-call,
  post-attempt, post-parse, and post-dispatch policy-state checkpoint indexes;
- parsed-action-candidate, environment-resume, sealed-action-intent, and sealed unsuccessful-result
  indexes;
- ordered environment actions, screenshot digests, rewards, termination/truncation states, and
  privileged diagnostic events;
- task-level scores, summary metrics, paired comparisons, uncertainty estimates, and exploratory
  analyses;
- environment, dependency, code-revision, price-catalog, and screen manifests;
- call-cap plan, actual provider calls, tokens, attributed cost, and latency; and
- an integrity report that rereads and verifies every referenced byte.

The manifest distinguishes model calls from environment actions and provider attempts from
completed responses. Unknown usage, price, latency, source identity, or artifact digest fails the
relevant evidence check rather than receiving an estimate.

The evidence store keeps access-controlled authoritative objects for capture-scrubbed canonical
provider records, policy-state checkpoints, `TaskSpec` records, environment-resume records, and
environment manifests. The canonical attempt record is the sole parser and recovery input; initial
execution and recovery read the same versioned bytes. Authoritative task and environment objects
preserve `Backend.app_url` and every other launch value required for replay and integrity
verification; they are the only inputs to replay. Credentials, API keys, authorization headers, and
secret-manager values are excluded at the capture boundary and never enter an authoritative object,
derivative, log, or manifest. Provider adapters retain only the canonical request and response
fields required by the frozen evidence schema.

A deterministic redaction transform produces a publishable derivative that removes hostnames,
usernames, account IDs, and private provider or policy-state fields. A relation record binds the
canonical authoritative digest, derivative digest, and redaction-policy version. Only the
derivative and relation record enter publishable or checked-in evidence; access to the authoritative
object is separately controlled. D5.2 must freeze these schemas, canonical serialization, capture
exclusions, access rules, redaction transform, and content-binding procedure before any real
provider response, policy-state checkpoint, or runtime launch record is stored.

## Platform boundary

The implemented Milestone 4 flow evaluates the frozen single-step grounding workload. It does not
currently establish stateful policy execution, sequential policy-state resume, or per-action raw
response lineage for v5. V5 therefore needs a separate versioned flow and schemas after the
Milestone 4 human gate or an explicit human sequencing exception.

V5 work must not modify frozen Sprint 3 evidence, reinterpret existing platform runs, or change the
meaning of an existing gate policy. The stateful flow may reuse immutable storage, MLflow lineage,
control-plane approval, and exact-version policy packaging only after compatibility is demonstrated
with new tests and schema versions.

## Call budgets and paid-call gates

No numeric paid-call cap is approved by this document. Before requesting approval, a free plan-only
command must compute, per policy and phase:

```text
environment_action_cap = sum(task.max_episode_steps)
provider_call_cap = sum(task.max_episode_steps * policy.max_provider_calls_per_action)
```

The plan reports separate caps for calibration, confirmatory primary evaluation, stateless
ablation, and reliability repeats. It also reports known deterministic cache reuse without
subtracting uncertain future hits from the approved cap.

Every provider, model/snapshot or alias, policy package, phase, and cap requires explicit human
approval. Calibration approval does not authorize confirmatory calls. Calls stop immediately when
the approved cap is reached; incomplete tasks remain incomplete evidence.

## Failure routing

- **Transport, parse, adapter, invalid-action, unknown-price, or evidence-integrity failure:**
  freeze the run and perform a no-call audit before changing task difficulty or policy behavior.
- **Human reviewer cannot solve an admitted development task from pixels and instruction:** remove
  the task from admission, repair the generator-level defect under a new version, and repeat all
  no-cost validation.
- **Calibration ceiling:** if the strongest approved policies saturate the frontier band, adjust
  capability-level generator knobs under a new version and repeat calibration after approval.
- **Calibration floor:** if all real policies fail while humans and the golden policy succeed,
  inspect action horizon, instruction sufficiency, adapter behavior, and individual diagnostics
  before increasing or reducing difficulty.
- **Low discrimination:** if items do not separate the policy panel, revise generator-level knobs;
  do not hand-edit items around one model's outputs.
- **Confirmatory ceiling, floor, null result, or negative result:** report it unchanged. No task,
  threshold, exclusion, retry, or policy may be altered after confirmatory output is observed.
- **Incomplete confirmatory run:** report the retained failures and missing assignments. It cannot
  become a complete benchmark claim through denominator changes.

## Delivery sequence

| ID | Owner | Deliverable | Stop condition |
|---|---|---|---|
| D5.1 | **YOU / PAIR** | Approve the end-to-end policy construct, raw primary condition, scope, and sequencing relative to D4.12 | No implementation before explicit approval |
| D5.2 | **AGENT · high** | Freeze task, generator, policy, canonical provider-response, state-reducer, trace, attempt-journal, parsed-candidate, environment-resume, action-intent, dispatch, authoritative/redacted manifest, policy-sandbox, metric, and explicit seed-list contracts | Interface, security-boundary, and backend-resume review required before application work |
| D5.3 | **AGENT · high** | Implement six deterministic generator families, versioned app states, evaluator fixtures, and golden policies | Stop if a core PixelGym invariant would need to change |
| D5.4 | **AGENT · high** | Build no-cost determinism, mutation, reward-hacking, replay, and admission evidence | Human inspects the development sample |
| D5.5 | **AGENT · high** | Implement the stateful policy harness, canonical-response persistence, exact policy-state recovery, v5 checkpoint/restore or reconnect support for FakeBackend and OSWorldBackend, resume boundaries, call caps, and no-cost fake policies | No real provider call; stop if exact backend-state recovery cannot be proven |
| D5.6 | **PAIR** | Freeze the calibration policy panel, prompts, adapters, prices, retry rules, call caps, and routing | Explicit paid-call approval required |
| D5.7 | **AGENT · high** | Run only the approved calibration and generate immutable calibration evidence | Stop at approved cap and await human review |
| D5.8 | **YOU / PAIR** | Approve generator revisions, item-band targets, minimum relevant difference, power target, final seeds, policy candidates, and confirmatory cap | Second explicit paid-call approval required |
| D5.9 | **AGENT · high** | Run the frozen confirmatory evaluation and generate the report entirely from stored evidence | No post-result tuning or rerun outside the reliability protocol |
| D5.10 | **YOU** | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | Agent does not declare the human gate passed |

## Test strategy

Fast unit and property tests must run without a browser, network, OSWorld, wall-clock sleeps,
provider credentials, or model calls. They cover:

- generator determinism, canonical identity, split disjointness, family allocation, difficulty
  bounds, typed-character limits, and robustness-pair semantic identity;
- policy-state reset, absence of cross-episode memory, action schema validation, internal call
  caps, pre-send attempt persistence, unknown-outcome reconciliation, post-attempt and post-dispatch
  state reconstruction, attempt identity, and no hidden retry;
- policy-sandbox denial of external search and arbitrary URLs, browser/DOM inspection, shell
  execution, shared or cross-policy channels, and all policy egress except the configured fake or
  provider endpoint, while required backend application and controller traffic remains available;
- exact reward timing, premature and wrong commits, repeated submissions, step-after-end behavior,
  termination versus truncation, and privileged diagnostic isolation;
- golden, recovery, near-miss, mutation, and replay trace validation;
- coordinate adapters, normalized-grid transforms, boundary rejection, and distinct policy
  identity;
- task and policy manifest hashing, attempt-started-before-send, canonical-response-before-policy
  ordering, byte-identical canonical parser input during initial execution and recovery, sealed
  parser failures and action intents, state reconstruction at every attempt/action boundary, resume
  idempotency, authoritative replay, authoritative-to-redacted content binding, integrity
  verification, secret-sentinel exclusion from every persisted artifact and log, redaction
  boundaries, and missing-evidence failure;
- metric denominators, Wilson intervals, paired tables, exact McNemar calculations, clustered
  bootstrap reproducibility, reliability separation, and display rounding; and
- call-cap formulas for every phase and policy configuration.

Browser integration tests replay every development trace twice against the deterministic task app
and compare the corresponding screenshots bitwise. A smaller pytest-marked integration set
exercises the stateful flow with a no-cost policy, forced interruptions at each supported
side-effect boundary—including after attempt-started, after provider receipt but before response
persistence, after canonical response persistence but before the post-attempt checkpoint, after
every terminal provider-attempt record, during pure parsing before outcome persistence, after
`parsed_action_candidate` but before result or intent sealing, after parser failure, and immediately
before action dispatch—and proof that resume does not duplicate provider calls or actions. Every
terminal-attempt interruption must restore byte-identical policy state before another attempt or the
parser runs. The parser-recovery test requires byte-identical canonical input and output.
Candidate-record recovery must seal the result or intent without invoking the parser.

The stateful fake policy must prove that resume after attempt, candidate, intent, or dispatch commit
reconstructs the exact policy state for the next operation. A process-restart test must restore or
reconnect the bound FakeBackend state after intent sealing, verify identical task, step,
application-state, and screenshot digests, and execute the stored action exactly once. Equivalent
pytest-marked OSWorldBackend restart/reconnect evidence is required before any real provider run.
Interruption after dispatch-started without a proven idempotent backend transaction must remain an
infrastructure failure. An unresolved post-send provider attempt must reconcile under the frozen
fake mechanism or remain an infrastructure failure without another request.

Any real provider smoke or calibration call is excluded from automated tests and remains a human
gate.

## Risks and limitations

| Risk | Mitigation and residual limitation |
|---|---|
| Longer horizons turn small grounding errors into episode failure | Report critical-decision accuracy and path overhead beside exact success; exact completion remains primary because the construct is end-to-end competence |
| Character-by-character `KEY` actions dominate cost and difficulty | Bound required string lengths and report semantic decisions separately from low-level actions; v5 still does not test long-form text entry |
| Calibration overfits the current policy panel | Change generator-level distributions only, generate confirmatory tasks from a later frozen version, and retain unedited negative results; the single application still limits generalization |
| System-level results obscure base-model contribution | Version the complete policy and provide a frozen reference harness; results describe the policy system, not the base model alone |
| Provider aliases or nondeterminism change behavior | Pin snapshots when possible, disclose aliases, use a predeclared reliability subset, and never treat repeats as hidden retries |
| Policy-dependent paths prevent exhaustive screenshot enumeration | Require deterministic replay for declared valid and adversarial traces plus property tests; arbitrary wrong paths remain outside exhaustive visual capture |
| Robustness twins reduce statistical independence | Cluster paired variants in resampling and report their consistency separately |
| Synthetic tasks invite shortcut learning | Use held-out parameter combinations, target-independent variants, and diagnostic mutation policies; one synthetic app cannot establish broad GUI competence |
| Stateful resume can duplicate side effects or lose policy/backend state | Bind canonical attempt bytes, exact policy checkpoints, and verified backend restore/reconnect state at every supported boundary; otherwise retain an infrastructure failure without another request or action |
| Agent-side pixel processing blurs architecture comparisons | Treat all screenshot-derived internal processing as part of the policy identity and forbid non-pixel environment channels |

## Alternatives considered

### Run more policies on v4c

V4c remains useful calibration evidence, but its raw episode ceiling cannot distinguish a stronger
policy from the corrected Qwen run. More comparisons on the same ten tasks would improve model
coverage without fixing benchmark discrimination.

### Make v4c longer but keep stateless calls

This would continue to test environment-mediated visible memory and per-screen action selection. It
would not measure an end-to-end agent's own planning and state management, which is the selected
construct.

### Hand-author one larger static test set

A static set is straightforward to inspect but offers weak control over difficulty, narrow semantic
coverage, and greater exposure to item-specific tuning. Versioned parameterized generation provides
held-out combinations while preserving exact reproducibility.

### Use a continuously hidden dynamic benchmark

Secret tasks can reduce direct memorization, but an indefinitely private generator conflicts with
this repository's local-first reproducibility and evidence goals. V5 freezes versioned generator
code, parameters, and seeds before each confirmatory run and treats later versions as new
benchmarks.

### Combine success, efficiency, cost, and latency into one score

A composite can hide a failed task behind a favorable secondary metric. V5 keeps exact success
primary and reports the other dimensions separately under conjunctive gates.

## Research basis

The design adopts execution-based task completion from
[OSWorld](https://proceedings.neurips.cc/paper_files/paper/2024/hash/5d413e48f84dc61244b6be550f1cd8f5-Abstract-Datasets_and_Benchmarks_Track.html)
and [WebArena](https://openreview.net/pdf?id=rmiwIL98uQ), parameterized reproducible tasks and
robustness variants from [AndroidWorld](https://openreview.net/pdf?id=il5yUQsrjC), and the
separation of binary completion from procedural diagnostics demonstrated by
[DiscoveryWorld](https://proceedings.neurips.cc/paper_files/paper/2024/hash/13836f251823945316ae067350a5c366-Abstract-Datasets_and_Benchmarks_Track.html).

Item difficulty and discrimination are distinct properties. The calibration analysis follows that
principle without making Item Response Theory an automatic gate; see
[Vania et al., “Comparing Test Sets with Item Response Theory,” ACL 2021](https://aclanthology.org/2021.acl-long.92/).

## Done when

- [ ] The human explicitly approves D5.1 and sequencing relative to the separate D4.12 gate.
- [ ] Versioned schemas and disjoint development, calibration, and confirmatory seed lists are
  committed before task capture or model calls.
- [ ] Six generator families produce the frozen allocations and satisfy every admission rule.
- [ ] Every declared valid and adversarial trace replays twice with identical semantic state and
  bitwise-identical corresponding screenshots.
- [ ] Golden policies reach reward `1.0` exactly once; all declared near-miss and reward-hacking
  mutations remain at `0.0`.
- [ ] Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt
  reconciliation, post-attempt and post-dispatch state reconstruction, canonical parser-input
  recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state
  restore or reconnect, pre-dispatch interruption, invalid output, call caps, resume, authoritative
  replay, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests.
- [ ] The documented fast suite passes without browser, network, OSWorld, provider credentials, or
  model calls.
- [ ] A free plan-only command reports exact action and provider-call caps for each approved phase.
- [ ] The agent stops for explicit calibration approval and again for explicit confirmatory
  approval.
- [ ] The confirmatory report is generated only from sealed evidence, retains every failure, and
  separates primary, diagnostic, ablation, reliability, and exploratory results.
- [ ] The agent reports raw milestone-gate commands, exit statuses, counts, runtimes, and full
  output without declaring the human gate passed or approving public wording.
