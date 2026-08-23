# Grounding v4c Pilot — Longer-Horizon Episodes with Deferred Visible Dependencies

**Status:** design only — no implementation, capture, or model call is authorized by this document

**Primary calibration model:** `gpt-5.6-luna`

**Proposed protocol identifier:** `pixelgym-grounding-v4c-pilot`

## 1. Routing basis

The frozen v4b Luna run produced 9/10 raw episode successes and 10/10 marks episode successes,
with zero request, parse, and invalid-action failures and 60/60 marks proposal coverage
([offline results](../artifacts/grounding-v4b-pilot-results-luna.json)). Under the preregistered
v4b rule — either condition at 9–10/10 — the stored routing decision is
`design_longer_horizon_successor`. The v4b episodes, captures, and predictions remain frozen;
v4c is a new protocol, not a tuned v4b rerun. These ten-episode calibration results are not a
public benchmark claim.

V4b showed that three locally decidable decisions per episode do not bind the calibration model.
V4c therefore lengthens the horizon and, critically, makes at least one later decision depend on
information that was visible in an earlier state — while every per-step request stays stateless.

## 2. Frozen pilot shape

- Ten deterministic episodes use seeds 60–69, disjoint from all prior grounding seeds
  (v1 frozen 0–19, v3 calibration 20–23, v4 30–31, v4b 40–49, golden-trajectory seed 7).
- Episodes require between six and eight correct semantic decisions. The per-family decision
  count is frozen at capture time:

| Family | Episodes | Decisions | Structure |
|---|---:|---:|---|
| Reference carry: read → pin → traverse → apply → confirm | 4 | 6 | Read a reference value on the intake panel, commit it to the visible pinned-reference chip, traverse two panels where the value is no longer shown at its source, use the chip to select among matching rows, apply the visible policy, confirm |
| Reconcile with deferred evidence | 3 | 7 | Note a discriminating attribute across conflicting visible records, commit it, inspect two candidate records in turn, reject the mismatch, resolve the match, confirm the resolution |
| Constraint-carrying repair chain | 3 | 8 | Read an early visible constraint, commit it, diagnose a later validation failure against the committed constraint, select the implicated control, apply the offered repair, verify, resubmit, confirm |

- The environment allows each episode at most **decisions + 2** actions. The two extra actions
  exist only for preregistered visible recovery states; they are not hidden retries.
- The pilot remains click-only. Each model response is the existing fixed action mapping with
  `action_type=CLICK`, bounded integer `x`/`y`, and valid inert values for the other fields.
  The action vocabulary, key allowlist, and coordinate bounds are unchanged from v4b.
- Each action is validated by the existing `PixelGuiEnv` action contract before backend
  execution. Invalid actions are recorded and end that condition as unsuccessful.
- Exact task completion is checked only by the privileged host-side evaluator. Intermediate
  screenshots, banners, pinned chips, and checkpoint progress never award reward.

The ten episode specifications must be committed before any model call. Each specification fixes
the initial task, every reachable screenshot hash, valid and recovery transitions, terminal
submission, decision count, and maximum step count. No episode may be edited after viewing a
prediction.

## 3. Deferred visible dependency

Every episode contains exactly one **deferred dependency**: a discriminating value visible in a
state at decision one or two that a later decision requires, with at least three intervening
decisions during which the value is not visible at its source.

Because per-step requests are stateless (section 4), the GUI is the only memory channel:

- Each episode offers a click-only **commit control** (for example, pinning a reference into a
  fixed on-screen chip region) that renders the value into a persistent visible artifact. The
  commit is itself one of the episode's semantic decisions.
- **Stateless solvability invariant:** on the correct path, every decision is fully determined by
  the current screenshot plus the fixed episode instruction. A scripted golden trajectory per
  episode, replayed through the normal backend/environment boundary, must reach reward `1.0`
  exactly once before any model call. This replay is free and makes no model calls.
- If the model skips the commit, the deferred-consumer state is ambiguous by construction: it
  presents at least three candidate controls that are plausible without the carried value and
  exactly one that is correct with it. This ambiguity is the preregistered intended failure mode,
  not a capture defect, and it bounds per-step guessing at that decision to at most one in three.
- The pinned-chip region has fixed CSS position and dimensions and obeys the existing determinism
  rules: no animation, transitions, clocks, or system-dependent fonts.

Two preregistered diagnostics separate failure causes: **commit checkpoint** (was the carrier
committed) and **carrier utilization** (given a visible carrier, was the deferred decision
correct). Both are diagnostic only and cannot produce reward.

## 4. Stateless per-step requests

- One fresh structured-output request per environment step; no conversation state, no tool use.
- Each request contains exactly the current screenshot and the frozen episode instruction. The
  instruction text is identical at every step of an episode; only the image varies. No step
  index, action history, prior screenshot, or prior model output is included.
- Request schema: the exact four-field PixelGym action mapping used in v4b.
- No hidden retry for transport, refusal, invalid JSON, schema failure, or out-of-range action.
- Identical (screenshot digest, condition) pairs may deterministically reuse a stored response,
  as in v4b. Reuse counts are disclosed in the plan-only report and the results artifact.

## 5. Paired conditions

Each episode is reset and run independently under two conditions:

1. **Raw:** the current unmodified 1024×768 screenshot.
2. **Marks:** the same state with every visible actionable control outlined and numbered.

Marks are generated at build time without consulting the episode's next correct action or its
deferred dependency. Every reachable raw screenshot is mapped by its SHA-256 digest to a prebuilt
marked image; unknown digests fail closed. The evaluation adapter may look up an overlay by
digest but must not import or call capture-time DOM instrumentation. Proposal coverage is
validated over every reachable state before evaluation and reported separately from policy
success. Target identity is joined only in the privileged scorer after the complete candidate
set and overlay are frozen.

## 6. Model and call budget

- Model: `gpt-5.6-luna`. Reasoning effort: `low`. Same parameters as v4b.
- Per-condition upper bound: the sum of per-episode maxima,
  4×(6+2) + 3×(7+2) + 3×(8+2) = 89 actions.
- Hard cap: 89 × 2 conditions = **178 paid calls**. The runner records the approved bound and the
  actual count and refuses to exceed the bound across both conditions.
- A free `--plan-only` command must validate the frozen episode/state graph and golden
  trajectories, report proposal coverage, report zero or known cache hits, and report an upper
  bound of exactly 178 calls before approval is requested.
- Execution requires a new explicit human approval for up to 178 Luna calls. The v4b approval
  does not carry forward. Any other model or any expansion requires another approval.

## 7. Metrics and preregistered routing

The primary metric is exact episode success, reported as a paired raw and marks count out of ten.
Secondary diagnostics are the commit checkpoint, carrier utilization, checkpoint completion,
action count to success, termination versus truncation, request failures, parse failures,
invalid-action failures, cache reuse, and proposal coverage.

Routing is fixed before the pilot:

- Raw 4–7/10 and marks 5–8/10, with no request/parse/invalid-action failure: freeze v4c and
  request separate approval for any ceiling comparison.
- Either condition at 9–10/10: report saturation and stop. Any successor design requires an
  explicit new human scope decision under D1.1; this plan does not pre-authorize one.
- Either condition below 4/10, any request/parse/invalid-action failure, any unknown screenshot
  hash, or proposal coverage below 100%: perform a floor/transport audit before changing task
  difficulty. The audit checks stored response validity, action schema, coordinate frame,
  screenshot-hash lookup, transition correctness, commit-checkpoint attribution, and the
  semantic-nearest valid action. It makes no model calls.
- Any other score combination receives human review; it does not silently select a new dial.

## 8. Evidence and implementation boundary

Implementation is a later, separately reviewed change. It must produce immutable episode specs,
raw and marked state images, target-independent candidate records, transition-graph validation,
per-episode golden-trajectory replays, two bitwise-identical capture passes, a plan-only call
report, predictions, and an offline-generated results artifact. Every number in a manifest or
report must be derived from those stored records.

V4c must not change `PixelGuiEnv`, its observation/action spaces, reward timing, evaluator
boundary, or any frozen v4 or v4b artifact. A captured-state helper may validate the finite
graph, but the scored run must dispatch model actions through the normal backend/environment
boundary. No report may call the pilot passed or failed; agents report commands, exit statuses,
counts, and raw outputs, and the human owns the verdict.

## 9. Done when

- [ ] The human approves this scope before implementation begins.
- [ ] Ten episode specs with the frozen 4/3/3 family allocation and 6/7/8 decision counts are
  committed before model evaluation.
- [ ] Every episode's golden trajectory reaches reward `1.0` exactly once through the normal
  environment boundary, without model calls.
- [ ] All reachable states, recovery transitions, and pinned-chip renderings are deterministic
  and captured twice with bitwise-identical output.
- [ ] Candidate generation is target- and dependency-independent, and proposal coverage is 100%
  for every reachable state.
- [ ] Unit tests cover wrong episode/decision/action counts, duplicate states, unknown screenshot
  hashes, target leakage, dependency leakage into marks, invalid actions, premature reward,
  missing-commit ambiguity structure, call-cap enforcement, and routing.
- [ ] Fast tests pass without network, a browser, OSWorld, or paid model calls.
- [ ] The free plan reports an upper bound of exactly 178 calls.
- [ ] The agent stops for explicit paid-call approval.
