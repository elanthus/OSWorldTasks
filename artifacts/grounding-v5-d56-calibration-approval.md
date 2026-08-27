# PixelGym v5 D5.6 calibration approval packet

**Status:** panel, clean-set size, and aggregate budget approved by the owner on 2026-08-26; the
replacement smoke completed; the four-slot calibration stopped during Slot A and is frozen; a
B/C/D-only successor package is implemented but its exact plan digest remains unapproved

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
| Price catalog | Provider-published prices captured with source URL and effective timestamp | Refreshed for Google Vertex, Alibaba, and DeepInfra on 2026-08-26 |
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
| C | OpenRouter → DeepInfra FP8 only | `meta-llama/llama-4-scout` | Stateful visible-action history | `native-1024x768` | Lower-band policy candidate | Approved; adapter requires smoke validation |
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
python scripts/run_grounding_v5_d56_calibration.py \
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
python scripts/run_grounding_v5_d56_bcd_calibration.py \
  --plan-only \
  --smoke-output artifacts/grounding-v5-d56-panel-smoke-run-v4 \
  --frozen-calibration-output artifacts/grounding-v5-d56-calibration-run-v2 \
  --output artifacts/grounding-v5-d56-bcd-calibration-plan.json
```

The printed digest requires a fresh, exact owner approval. The earlier smoke and calibration
approvals do not authorize the successor plan.

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
