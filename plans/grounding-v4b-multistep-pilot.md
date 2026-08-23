# Grounding v4b Pilot — Short Closed-Loop Episodes

**Status:** implemented, captured, and evaluated; preregistered successor-design route recorded

**Primary calibration model:** `gpt-5.6-luna`

**Proposed protocol identifier:** `pixelgym-grounding-v4b-pilot`

## 1. Routing basis

The frozen v4 Luna run produced 9/10 correct raw predictions and 9/10 correct marks-aided
predictions, with zero request failures and zero parse failures. Both conditions exceed the
preregistered 85% saturation threshold, so the v4 rule routes to a separate multi-step design.
The stored v4 inputs and predictions remain unchanged; v4b is a new protocol, not a tuned v4
rerun. These ten-example calibration results are not a public benchmark claim.

V4b tests whether a pixel-only policy can use visible consequences across a short episode. It
does not make the single screenshot harder merely by adding geometric clutter.

## 2. Frozen pilot shape

- Ten deterministic episodes use seeds 40–49, disjoint from all prior grounding calibration
  seeds.
- Every successful episode requires exactly three correct semantic decisions.
- The environment allows at most four model actions. The fourth action exists only for a
  preregistered visible recovery state; it is not a hidden retry.
- The pilot remains click-only. Each model response is the existing fixed action mapping with
  `action_type=CLICK`, bounded integer `x`/`y`, and valid inert values for the other fields.
- Each action is validated by the existing `PixelGuiEnv` action contract before backend
  execution. Invalid actions are recorded and end that condition as unsuccessful.
- The screenshot returned by the environment is the only observation. The task instruction may
  be repeated in the prompt, but no DOM, accessibility tree, target box, state ID, expected
  answer, or evaluator diagnostic is exposed.
- Exact task completion is checked only by the privileged host-side evaluator. Intermediate
  screenshots, banners, and checkpoint progress never award reward.

The family allocation is frozen at capture time:

| Family | Episodes | Three-decision structure |
|---|---:|---|
| Triage → route → confirm | 4 | Compare visible request rows, open one, apply the visible policy, confirm the route |
| Reconcile → inspect → resolve | 3 | Match conflicting visible records, inspect the selected record, choose the safe resolution |
| Diagnose → repair → resubmit | 3 | Read visible failure feedback, select the implicated control, apply the offered repair, resubmit |

The ten episode specifications must be committed before any model call. Each specification fixes
the initial task, every reachable screenshot hash, valid transition, recovery transition, terminal
submission, and maximum step count. No episode may be edited after viewing a prediction.

## 3. Paired conditions

Each episode is reset and run independently under two conditions:

1. **Raw:** the current unmodified 1024×768 screenshot.
2. **Marks:** the same state with every visible actionable control outlined and numbered.

Marks are generated at build time without consulting the episode's next correct action. Every
reachable raw screenshot is mapped by its SHA-256 digest to a prebuilt marked image. The evaluation
adapter may look up an overlay by screenshot digest, but it must not import or call capture-time DOM
instrumentation. Unknown screenshot hashes fail closed.

Proposal coverage is validated over every reachable state before evaluation and reported
separately from policy success. Target identity is joined only in the privileged scorer after the
complete candidate set and overlay are frozen.

## 4. Model and call budget

- Model: `gpt-5.6-luna`.
- Reasoning effort: `low`.
- One fresh structured-output request per environment step; no conversation state or tool use.
- Request schema: the exact four-field PixelGym action mapping.
- No hidden retry for transport, refusal, invalid JSON, schema failure, or out-of-range action.
- Maximum: 10 episodes × 2 conditions × 4 actions = **80 paid calls**.
- A free `--plan-only` command must validate the frozen episode/state graph, report zero or known
  cache hits, and report an 80-call upper bound before approval is requested.
- Execution requires a new explicit human approval for up to 80 Luna calls. This v4 approval does
  not carry forward. Any other model or any expansion requires another approval.

An episode that terminates in fewer than four steps uses fewer calls. The runner records both the
approved upper bound and the actual call count and refuses to exceed the bound across both
conditions.

## 5. Metrics and routing

The primary metric is exact episode success, reported as a paired raw and marks count out of ten.
Secondary diagnostics are checkpoint completion, action count to success, termination versus
truncation, request failures, parse failures, invalid-action failures, and proposal coverage. A
checkpoint metric is diagnostic only and cannot produce reward.

Routing is fixed before the pilot:

- Raw 5–7/10 and marks 6–8/10, with no request/parse/invalid-action failure: freeze v4b and request
  separate approval for any ceiling comparison.
- Either condition at 9–10/10: report saturation and design at most one longer-horizon successor;
  do not alter these ten episodes.
- Either condition below 5/10, any request/parse/invalid-action failure, any unknown screenshot
  hash, or proposal coverage below 100%: perform a floor/transport audit before changing task
  difficulty.
- Any other score combination receives human review; it does not silently select a new dial.

The floor audit checks stored response validity, action schema, coordinate frame, screenshot-hash
lookup, transition correctness, and the semantic-nearest valid action. It makes no model calls.

## 6. Evidence and implementation boundary

Implementation is a later, separately reviewed change. It must produce immutable episode specs,
raw and marked state images, target-independent candidate records, transition-graph validation,
two bitwise-identical capture passes, a plan-only call report, predictions, and an offline-generated
results artifact. Every number in a manifest or report must be derived from those stored records.

V4b must not change `PixelGuiEnv`, its observation/action spaces, reward timing, evaluator boundary,
or the frozen v4 artifacts. A captured-state helper may validate the finite graph, but the scored
run must dispatch model actions through the normal backend/environment boundary.

## 7. Done when

- [ ] The human approves this scope before implementation begins.
- [ ] Ten episode specs and their 4/3/3 family allocation are frozen before model evaluation.
- [ ] All reachable states and recovery transitions are deterministic and captured twice with
  bitwise-identical output.
- [ ] Candidate generation is target-independent and proposal coverage is 100% for every reachable
  state.
- [ ] Unit tests cover wrong episode/action counts, duplicate states, unknown screenshot hashes,
  target leakage, invalid actions, premature reward, call-cap enforcement, and routing.
- [ ] Fast tests pass without network, a browser, OSWorld, or paid model calls.
- [ ] The free plan reports an upper bound of exactly 80 calls.
- [ ] The agent stops for explicit paid-call approval.

## 8. Recorded execution

The human explicitly authorized implementation, capture, and up to 80 Luna calls. The free
planner reported an 80-call upper bound, zero cached reachable-state requests, and 60/60 proposal
coverage before collection. The capped run then stored 63 action records: 62 new provider calls
and one same-state cache reuse. It used no hidden retry and remained below the approved ceiling.

The offline-generated result records raw success of 9/10 and marks success of 10/10, with no
request, parse, or invalid-action failure. All 60 actionable states had marks proposal coverage.
Under the frozen rule in section 5, this routes to `design_longer_horizon_successor`. This is a
calibration result, not a public benchmark claim or a human milestone-gate verdict.
