# PixelGym v5 D5.6 calibration approval packet

**Status:** the four-slot, B/C/D, native-coordinate Slot C, and normalized Llama development runs
are frozen; the owner selected `z-ai/glm-5.3-flash` for a one-episode comparison on 2026-08-27,
and its exact paid-call plan remains pending

**Primary reader:** the project owner freezing the v5 calibration panel and deciding whether to
authorize a capped calibration run

## Decision required

The owner has approved the design envelope: the four rows below, a 50-task calibration set that
excludes every task exposed by the Qwen pilot, and a $10 aggregate ceiling including prior spend.
This is not yet the exact paid-call approval required by D5.6. Before any new request, approve one
complete, content-bound package naming every policy system, provider endpoint, model snapshot or alias,
prompt and parser identity, price record, retry rule, and call cap. Approval of one package does not
authorize a different model, adapter, prompt, cap, confirmatory run, reliability repeat, or
stateless ablation.

The implemented contracts are defined by
[`PolicyManifest`](../pixelgym/grounding/v5/contracts.py),
[`SandboxManifest`](../pixelgym/grounding/v5/contracts.py), and
[`CallCaps`](../pixelgym/grounding/v5/contracts.py). The protocol and human stop conditions remain
authoritative in the [v5 benchmark plan](../plans/grounding-v5-agent-benchmark.md).

## Preconditions

The owner should approve calibration only after recording evidence for every row.

| Precondition | Required evidence | Current status |
|---|---|---|
| D4.12 milestone gate | Human verdict | Deliberately unpassed; the verdict remains null |
| D5.1 scope and sequencing | Explicit owner decision | Approved to proceed with v5 readiness work while D4.12 remains null |
| Frozen task partitions | Byte-reproducible development, calibration, and confirmatory manifests | Implemented; checked-in reproduction test added |
| Development usability review | Six-family review of legibility, instruction sufficiency, screenshot-only solvability, and leakage | Owner decision required |
| Fake-backend recovery | Interruption tests for every supported durable boundary | Implemented in the v5 unit suite |
| OS-level policy isolation | Real OS enforcement test with an allowed fake endpoint and denied unauthorized channels | Implemented and exercised on Darwin |
| OSWorld live reconnect | Real local OSWorld session accepts the current binding, rejects stale state, and closes cleanly | Exercised locally on 2026-08-25; opt-in integration test added |
| Real policy adapters | Provider transport, canonical response capture, parser, state reducer, and sandbox entry point | Four policy packages implemented; new Google Vertex Slot A package pending exact smoke approval |
| Exact runtime | Clean dependency lock, source revision, runtime digest, and `pip check` result | Generator implemented; exact identities bind after the implementation commit |
| Price catalog | Provider-published prices captured with source URL and effective timestamp | DeepInfra Llama 4 Scout and Novita GLM 5.3 Flash prices checked on 2026-08-27; other panel prices refreshed on 2026-08-26 |
| Plan-only caps | Per-policy phase caps generated from the final manifest | Four-policy 50-task planner implemented; exact digests bind after smoke evidence |
| Smoke evidence | Approved development-only real-provider smoke tests retain every attempt and failure | Replacement four-slot smoke completed and is frozen at `artifacts/grounding-v5-d56-panel-smoke-run-v4/` |

## Policy panel decision

The panel must contain at least four meaningfully distinct policy systems. At least one approved
model must appear in both stateful and stateless harnesses so calibration can test whether the task
bank exercises episode-local memory. Complete one row per immutable `policy_id`; do not use a base
model name as the policy identity.

| Slot | Provider route | Disclosed alias | Harness | Coordinate adapter | Purpose | Design decision |
|---|---|---|---|---|---|---|
| A | OpenRouter → Google Vertex Global only | `google/gemini-3.7-flash` | Stateful visible-action history | `normalized-1000x1000` | Strong-policy candidate | Approved; omit unsupported `temperature` |
| B | OpenRouter → Alibaba only | `qwen/qwen3-vl-8b-instruct` | Stateful visible-action history | `normalized-1000x1000` | Mid-band visual policy candidate | Approved |
| C | OpenRouter → DeepInfra FP8 only | `meta-llama/llama-4-scout` | Stateful visible-action history | `normalized-1000x1000` | Lower-band policy candidate | One development trial reached stage 1, then looped on the text field; frozen on HTTP 429 |
| D | OpenRouter → Alibaba only | `qwen/qwen3-vl-8b-instruct` | Stateless within each episode | `normalized-1000x1000` | Isolate episode-local state use | Approved |

Qwen appears twice intentionally. Slots B and D freeze the same model, route, coordinate adapter,
inference parameters, and task order; their memory-policy and state-reducer identities differ. This
is the controlled stateful/stateless comparison required by the benchmark plan.

For each row, attach a canonical policy manifest that supplies every field currently enforced by
`PolicyManifest`:

- exact provider and model identity, including whether the model is a pinned snapshot;
- harness, dependency-lock, system-prompt, and coordinate-adapter digests;
- task renderer, canonical response, state reducer, parser, and memory-policy versions;
- inference parameters and context limit;
- maximum model attempts, cancellation requests, and reconciliation requests per action;
- request deadline, cancellation mode, reconciliation deadline, and transport retry rule;
- sandbox runtime digest, credential-free provider origin, allowlist digest, and denied
  capabilities;
- code revision, clean-worktree rule, disabled cross-episode cache, and disabled SDK/proxy
  automatic retries.

Changing any field requires a new `policy_id`, a new cap plan, and a new approval.

## Price and cost decision

The refreshed route-specific token prices are:

| Route | Prompt or image-input token | Completion token | Frozen per-request maximum |
|---|---:|---:|---:|
| Google Vertex Global / Gemini 3.7 Flash | $0.000000375 | $0.000001875 | $0.055296000 |
| Alibaba / Qwen3-VL 8B | $0.000000117 | $0.000000455 | $0.016719872 |
| DeepInfra FP8 / Llama 4 Scout | $0.0000001 | $0.0000003 | $0.013926400 |
| Novita FP8 / GLM 5.3 Flash candidate | $0.000000075 | $0.00000025 | $0.010547200 |

Each request freezes at most 126,976 prompt tokens plus 4,096 completion tokens within a 131,072
policy context limit. These are conservative request guards, not expected costs. The uncapped
worst-case sum across all 11,448 possible requests is $293.819056128, so the $10 shared ledger—not
that uncapped sum—is the binding run stop. A request is blocked before transmission when its own
worst-case maximum no longer fits under the remaining aggregate balance.

Each generated price record also contains:

- provider, model snapshot or alias, billing unit, currency, and region when applicable;
- input-image, input-token, output-token, and request charges that can apply;
- provider source URL and the UTC timestamp at which the price was read;
- the rule for requests with unknown usage or price; the v5 evidence contract requires these to
  fail closed rather than receive an estimate.

Do not place API keys, authorization headers, account identifiers, or secret-manager references in
the price record or approval packet.

## Call-cap plan

After all four panel smokes verify, generate the exact no-call calibration plan from the repository
root:

```bash
.venv/bin/python scripts/run_grounding_v5_d56_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --output artifacts/grounding-v5-d56-calibration-plan-v2.json
```

The output must report `provider_calls_made: 0` and separate caps for calibration, confirmatory
primary evaluation, the stateless ablation, and reliability repeats. Review and approve only the
calibration caps at D5.6. Confirmatory, ablation, and reliability caps remain unauthorized until
their later gates.

Record the approved calibration limits for each policy:

| Slot / exact policy ID | Environment actions | Model attempts | Provider control requests | Total wire requests | Spend rule | Design decision |
|---|---:|---:|---:|---:|---:|---|
| A / generated after clean commit | 1,431 | 2,862 | 0 | 2,862 | Shared $10 ledger; $0.055296/request guard | Approved |
| B / generated after clean commit | 1,431 | 2,862 | 0 | 2,862 | Shared $10 ledger; $0.016719872/request guard | Approved |
| C / generated after clean commit | 1,431 | 2,862 | 0 | 2,862 | Shared $10 ledger; $0.0139264/request guard | Approved |
| D / generated after clean commit | 1,431 | 2,862 | 0 | 2,862 | Shared $10 ledger; $0.016719872/request guard | Approved |
| **Aggregate** | **5,724** | **11,448** | **0** | **11,448** | **$10 including prior spend** | **Approved envelope** |

The exact policy IDs and plan digests remain pending because they bind the clean implementation
revision and verified smoke evidence. Until those are separately approved, the table authorizes
zero calls.

## B/C/D successor decision

The owner selected Slots B, C, and D after the approved four-slot calibration stopped on the 33rd
Slot A assignment. The predecessor run is immutable at
`artifacts/grounding-v5-d56-calibration-run-v2/`. Its terminal request crossed the send boundary,
then reached the runner deadline without a response. The journal classifies that outcome as
`unknown_outcome_infrastructure_failure` with failure code `runner_request_deadline`; it is not
eligible for retry. The successor neither resumes Slot A nor replaces any Slot A assignment.

The new approval package must bind the predecessor plan, summary, journal, terminal event, and
actual aggregate spend before it can authorize any call. It schedules the same frozen 50-task
partition for the three previously unattempted policy slots:

| Slot | Environment actions | Model attempts | Control requests | Wire requests | Uncapped request maximum |
|---|---:|---:|---:|---:|---:|
| B | 1,431 | 2,862 | 0 | 2,862 | $47.852273664 |
| C | 1,431 | 2,862 | 0 | 2,862 | $39.857356800 |
| D | 1,431 | 2,862 | 0 | 2,862 | $47.852273664 |
| **B/C/D total** | **4,293** | **8,586** | **0** | **8,586** | **$135.561904128** |

The shared ledger has already attributed `$2.032875185`, leaving `$7.967124815` under the approved
`$10.00` ceiling. The ledger, rather than the uncapped maximum, remains binding. Generate the exact
no-call successor plan only after committing the implementation:

```bash
.venv/bin/python scripts/run_grounding_v5_d56_bcd_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --frozen-calibration-output artifacts/grounding-v5-d56-calibration-run-v2 \
  --output artifacts/grounding-v5-d56-bcd-calibration-plan.json
```

The owner approved the exact B/C/D plan digest
`sha256:880fa35de9616a5a46a766ab9babecf495315d4e4ff3d46e5c1eeb49809e68a9`.
The run attempted the first Slot B assignment, completed 24 actions, and received HTTP 429 on its
25th request. The terminal attempt has no canonical response or usage and is sealed as
`unknown_outcome_infrastructure_failure` with failure code `provider_request_unknown`. The run is
immutable at `artifacts/grounding-v5-d56-bcd-calibration-run/`; it cannot be resumed or retried.

## Slot C successor decision

The owner selected only Slot C after the B/C/D run froze. The new package schedules the same frozen
50-task partition for `meta-llama/llama-4-scout` on DeepInfra FP8, using the stateful native-pixel
policy. It neither resumes nor replaces any Slot A or Slot B request.

| Slot | Environment actions | Model attempts | Control requests | Wire requests | Uncapped request maximum |
|---|---:|---:|---:|---:|---:|
| C | 1,431 | 2,862 | 0 | 2,862 | $39.8573568 |

The shared ledger has attributed `$2.040917557`, leaving `$7.959082443` under the existing `$10.00`
ceiling. Generate the exact no-call Slot C plan only after committing the implementation:

```bash
.venv/bin/python scripts/run_grounding_v5_d56_c_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --frozen-calibration-output artifacts/grounding-v5-d56-calibration-run-v2 \
  --frozen-bcd-output artifacts/grounding-v5-d56-bcd-calibration-run \
  --output artifacts/grounding-v5-d56-c-calibration-plan.json
```

The owner approved exact plan
`sha256:beb618d74b79d93b12afe37347057850483252ffb173cbf37d03198d5d2e4c37`.
The native-coordinate run attempted 42 assignments: 41 reached their step limit without leaving
stage zero, and the 42nd stopped on an HTTP 429 unknown outcome. It recorded 1,179 environment
actions, 1,180 provider requests, zero successes, and `$0.440101900` incremental spend. Aggregate
spend is `$2.481019457`, leaving `$7.518980543`. Preserve
`artifacts/grounding-v5-d56-c-calibration-run/` unchanged; do not resume or retry it.

## Normalized Slot C development trial

The owner selected a new Slot C policy identity that changes only the coordinate adapter and its
declared input convention from native 1024×768 pixels to a normalized 1000×1000 grid. This is not a
resume or reinterpretation of the frozen native run.

The first paid check is one complete episode on development seed `5010`, which the earlier panel
smoke already exposed. It does not consume a calibration or confirmatory task. The episode permits
at most 28 environment actions, 56 model attempts or wire requests, zero control requests, and an
uncapped theoretical maximum of `$0.7798784`. The shared `$10.00` ledger remains binding.

The approved no-call plan was generated with:

```bash
.venv/bin/python scripts/run_grounding_v5_d56_c_normalized_trial.py \
  --plan-only \
  --frozen-c-output artifacts/grounding-v5-d56-c-calibration-run \
  --output artifacts/grounding-v5-d56-c-normalized-trial-plan.json
```

The owner approved plan
`sha256:1e87da05960de2b1ae44926f2d83b34c7e47e6a2871e1e22d293859036ff7d15`.
The normalized Llama run completed the first transition, then issued 18 more clicks that focused
the stage-1 text field instead of typing its visible code. Request 20 returned HTTP 429 with no
canonical response or usage. The run recorded 19 environment actions, 20 provider requests, zero
successes, and `$0.006320000` incremental spend. Aggregate spend is `$2.487339457`, leaving
`$7.512660543`. Preserve `artifacts/grounding-v5-d56-c-normalized-trial-run/` unchanged and do not
resume or retry it.

## GLM normalized development comparison

The owner selected `z-ai/glm-5.3-flash` as a comparison candidate, not yet as a replacement for the
frozen Slot C policy. Use the same already-exposed development seed `5010`, normalized coordinate
adapter, and stateful visible-action history. Pin OpenRouter to Novita FP8, disable fallbacks, and
require every declared request parameter. The Novita route was selected because its endpoint
advertised image input, structured responses, and the frozen `seed` parameter when checked on
2026-08-27.

The episode permits at most 28 environment actions, 56 model attempts or wire requests, zero
control requests, and an uncapped theoretical maximum of `$0.590643200`. It starts from aggregate
spend `$2.487339457`; the shared `$10.00` ledger remains binding. Generate the no-call plan only
after committing the implementation:

```bash
.venv/bin/python scripts/run_grounding_v5_d56_glm_normalized_trial.py \
  --plan-only \
  --frozen-llama-trial-output artifacts/grounding-v5-d56-c-normalized-trial-run \
  --output artifacts/grounding-v5-d56-glm-normalized-trial-plan.json
```

The planner must bind the normalized Llama trial's exact summary, journal, terminal event, and
aggregate spend, report `provider_calls_made: 0`, and name only development seed `5010`. Every prior
approval authorizes zero GLM requests; execution requires approval of the new printed digest.

## Calibration routing freeze

Before calls, freeze the decisions that follow calibration outcomes:

- **Ceiling:** revise generator-level capability knobs under a new generator version, then repeat
  no-call admission and request a new calibration approval.
- **Floor:** audit instruction sufficiency, action horizon, parser, adapter, and per-decision
  diagnostics before changing difficulty.
- **Low discrimination:** revise generator distributions rather than editing individual tasks
  around one model's output.
- **Infrastructure, transport, parse, adapter, price, or integrity failure:** freeze the run and
  perform a no-call audit. The only permitted post-response retry is the manifest-bound rule for a
  matching, empty `finish_reason=error` response with zero completion tokens and zero cost. Retain
  both attempts; a second matching response stops as infrastructure failure. A frozen pre-send
  no-response rule may also apply within every approved cap. Never retry an unknown outcome,
  silently retry any other failure, or alter task difficulty.
- **Mixed informative outcomes:** retain the complete matrix and proceed to the D5.8 power and
  final-freeze review.

Every response, invalid output, request failure, exhausted budget, and missing assignment remains
in the stored evidence and its applicable denominator.

## Approval record

The owner should provide one explicit record containing all of the following:

```text
D5.6 calibration package: APPROVED or NOT APPROVED
Approved policy IDs: <exact list>
Approved policy-manifest digests: <exact list>
Approved provider/model identities: <exact list>
Approved frozen partition-manifest digests (development, calibration, confirmatory): <exact list>
Approved calibration partition-manifest digest: <exact digest>
Approved price-record digests: <exact list>
Approved calibration cap-plan digests: <exact list>
Approved maximum attributed spend: <amount and currency>
Approved smoke-evidence digests: <exact list>
Approval timestamp: <UTC timestamp>
Approved by: <project owner>
```

Before accepting the approval, canonicalize and verify each referenced policy manifest and confirm
that its digest binds the approved provider endpoint, system-prompt digest, parser version, and
transport retry rule. Generate each cap plan from the exact approved partition-manifest files. The
planner verifies their embedded digests, derives caps from their stored `max_episode_steps` records
without regenerating tasks, rejects a calibration digest different from the separately supplied
approved digest, and binds the policy and partition digests into the plan digest. Confirm the
approved plan and its digest reproduce byte-for-byte and name the exact approved policy and
calibration partition digests.

An approval with a missing binding, identity, cap, price, partition, policy manifest, or smoke
evidence digest is incomplete and authorizes zero calibration calls.
