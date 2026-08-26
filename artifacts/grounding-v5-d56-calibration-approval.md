# PixelGym v5 D5.6 calibration approval packet

**Status:** draft for owner review; no provider call or spend is authorized

**Primary reader:** the project owner freezing the v5 calibration panel and deciding whether to
authorize a capped calibration run

## Decision required

Approve one complete, content-bound calibration package before any calibration task is sent to a
real provider. Approval must name every policy system, provider endpoint, model snapshot or alias,
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
| Real policy adapters | Provider transport, canonical response capture, parser, state reducer, and sandbox entry point | Not implemented or frozen |
| Exact runtime | Clean dependency lock, source revision, runtime digest, and `pip check` result | Not frozen |
| Price catalog | Provider-published prices captured with source URL and effective timestamp | Not selected |
| Plan-only caps | Per-policy phase caps generated from the final manifest | Pending final policy manifests |
| Smoke evidence | Approved development-only real-provider smoke tests retain every attempt and failure | Pending separate smoke approval |

## Policy panel decision

The panel must contain at least four meaningfully distinct policy systems. At least one approved
model must appear in both stateful and stateless harnesses so calibration can test whether the task
bank exercises episode-local memory. Complete one row per immutable `policy_id`; do not use a base
model name as the policy identity.

| Slot | Provider | Exact model snapshot or disclosed alias | Harness | Coordinate adapter | Purpose | Decision |
|---|---|---|---|---|---|---|
| A | TBD | TBD | Stateful | TBD | Strong-policy candidate | Pending |
| B | TBD | TBD | Stateful | TBD | Distinct provider or model family | Pending |
| C | TBD | TBD | Stateful | TBD | Expected mid- or lower-band policy | Pending |
| D | Same model as one stateful row | TBD | Stateless reference | Same tested adapter | Isolate episode-local state use | Pending |

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

Capture prices only after the provider and model are selected. Each price record must contain:

- provider, model snapshot or alias, billing unit, currency, and region when applicable;
- input-image, input-token, output-token, and request charges that can apply;
- provider source URL and the UTC timestamp at which the price was read;
- the rule for requests with unknown usage or price; the v5 evidence contract requires these to
  fail closed rather than receive an estimate.

Do not place API keys, authorization headers, account identifiers, or secret-manager references in
the price record or approval packet.

## Call-cap plan

Generate a free plan for each final policy manifest from the repository root:

```bash
python scripts/plan_grounding_v5_calls.py path/to/policy-manifest.json \
  --partition-manifest-directory artifacts/grounding-v5-manifests \
  --approved-calibration-partition-digest sha256:APPROVED_DIGEST
```

The output must report `provider_calls_made: 0` and separate caps for calibration, confirmatory
primary evaluation, the stateless ablation, and reliability repeats. Review and approve only the
calibration caps at D5.6. Confirmatory, ablation, and reliability caps remain unauthorized until
their later gates.

Record the approved calibration limits for each policy:

| Policy ID | Environment actions | Model attempts | Provider control requests | Total wire requests | Maximum attributed cost | Decision |
|---|---:|---:|---:|---:|---:|---|
| TBD | TBD | TBD | TBD | TBD | TBD | Pending |

## Calibration routing freeze

Before calls, freeze the decisions that follow calibration outcomes:

- **Ceiling:** revise generator-level capability knobs under a new generator version, then repeat
  no-cost admission and request a new calibration approval.
- **Floor:** audit instruction sufficiency, action horizon, parser, adapter, and per-decision
  diagnostics before changing difficulty.
- **Low discrimination:** revise generator distributions rather than editing individual tasks
  around one model's output.
- **Infrastructure, transport, parse, adapter, price, or integrity failure:** freeze the run and
  perform a no-call audit. The only permitted retry is the frozen retry rule for a pre-send
  failure proven to have produced no response, within every approved cap. Never retry an unknown
  outcome, silently retry another failure, or alter task difficulty.
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
