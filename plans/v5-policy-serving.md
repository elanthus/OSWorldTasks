# PixelGym v5 Policy Serving — Stateful Episode API on the Platform

**Status:** S1 approved by the owner on 2026-09-09; S2 (frozen contracts) delivered. This
document authorizes no model calls. Each later stage starts only in the order of the delivery
table.

**Primary reader:** the project owner deciding whether the Milestone 4 platform should serve v5
stateful policy systems, and under which contracts

**Proposed protocol identifier:** `pixelgym-serving-v2`

## Outcome

Serve an approved, exact-version v5 policy system through the platform's control plane so an
external environment can run whole episodes against it: reset with a task instruction, send
screenshots, receive validated PixelGym actions, report each dispatch result, and close. The
serving path must keep every v5 contract that the benchmark runner enforces — journaled attempts,
durable policy-state checkpoints, capped calls, sealed parser failures, credential exclusion, and
policy-egress isolation — and every Milestone 4 governance contract: human approval, exact-version
activation, previous-approved rollback, and immutable operational records.

The result is not a benchmark and not a public claim. It is the delivery half of the platform for a
different policy shape than the one it was built around.

## Why the current serving boundary cannot host a v5 policy

The v1 serving API (`/api/v1/ground`) is stateless. One request carries a screenshot and a target
string, the server renders the packaged prompt, calls a `ServingProvider.ground`, and returns one
point. The v1 runtime rejects anything but a raw-coordinate grounding policy
([service.py](../pixelgym/platform/service.py) `PolicyRuntime.activate`).

A v5 policy is a session. Its frozen interface is `reset(instruction) -> state`, then repeated
`build_request(state, screenshot)` → journaled provider attempt → `reduce_state` → `parse` →
`post_parse_state` → sealed action intent → `post_dispatch_state(state, action, result)`
([runner.py](../pixelgym/grounding/v5/runner.py) `StatefulPolicyPackage`). It emits `NOOP`,
`CLICK`, or `KEY` mappings, not points; it needs the dispatch result of its previous action before
it can act again; and it carries a `SandboxManifest`, attempt caps, a request deadline, and a
coordinate adapter as part of its identity.

The v5 plan's platform-boundary section already states that Milestone 4 does not establish stateful
policy execution or sequential policy-state resume, that v5 needs a separate versioned flow and
schemas, and that platform reuse is allowed only after compatibility is demonstrated with new tests
and schema versions ([v5 plan](grounding-v5-agent-benchmark.md#platform-boundary)). This note is
that proposal.

## Goals

- One approved v5 policy package is deployable, servable, and rollback-able through the existing
  control store, approval record, deployment coordinator, and audit log, with a new package kind.
- The serving path reuses the v5 per-step transaction rather than re-implementing it: the same
  attempt journal, state reducer, parser, checkpoint, and retry-classifier code that produced the
  calibration evidence, hosted by a serving session instead of the benchmark runner.
- Every served action is validated against the existing PixelGym action contract before it leaves
  the server. The server never executes an action, never sees the task application, and never
  learns expected answers.
- Provider spend is capped per episode and per deployment, and the caps are part of the approved
  package.
- Policy code runs under the same OS-level egress sandbox the benchmark requires; the serving app
  process stays outside that sandbox.
- Operational records are immutable, credential-free, and sufficient to reconstruct each episode's
  attempt and dispatch sequence from stored bytes.

## Non-goals

- Serving the Day 3 single-shot grounding policies through the new API. v1 stays as is.
- Running the environment inside the serving process. The caller owns the environment.
- A confirmatory or calibration evaluation. Candidate evidence for v5 packages comes from the v5
  evaluation path, which is a separate task (D5.9).
- Multi-replica or multi-tenant serving. The MVP remains the single combined control/serving
  process, as today.
- Any real or paid provider call during implementation or validation. Fake policies and a local
  fake provider endpoint are the only sources of traffic until a separate human approval.

## Proposed contracts

### Package kind and identity

Add `kind: "stateful-v5"` to the platform policy package alongside the existing grounding kind. A
v5 package binds, by digest, the v5 `PolicyManifest` (model, prompts, parser, reducer, memory
policy, coordinate adapter, inference parameters, attempt caps, request deadline, cancellation and
reconciliation settings, screen dimensions, action-schema and key-allowlist versions), the
`SandboxManifest` and endpoint-allowlist digest, the policy package source digest, the dependency
lock digest, and the evaluation evidence that produced the candidate. Changing any bound field
changes `policy_id`. The v1 renderer binding does not apply to this kind; the package carries no
`prompt_template_text`, and `verify_renderer_binding` is replaced by a v5 manifest digest check.

The approved-provider registry from issue #164 gains its first non-grounding `kind`. A
`stateful-v5` record names the v5 `PolicyManifest` digest instead of a bare model string, and the
same content-bound approval digest rule applies.

### Episode session API (`/api/v2`)

| Method and path | Body | Response |
|---|---|---|
| `POST /api/v2/episodes` | `task_instruction`, `screen_width`, `screen_height`, `client_episode_ref` | `episode_id`, policy/deployment/exact-policy identity, `max_steps`, action-schema and key-allowlist versions |
| `POST /api/v2/episodes/{id}/act` | `screenshot_base64`, `media_type`, `previous_intent_id`, `previous_result` (`reward`, `terminated`, `truncated`, `screenshot_sha256`) — omitted only on the first call | `intent_id`, `action` (`NOOP` / `CLICK x y` / `KEY index`) **or** `sealed_failure` (`parse_failure`, `invalid_action`, `request_failure`, `cap_reached`, `infrastructure_failure`), `attempt_count`, identity |
| `POST /api/v2/episodes/{id}/close` | `final_screenshot_base64`, `media_type`, `final_result` (`reward`, `terminated`, `truncated`) for the last issued intent, when one is outstanding | terminal summary: steps, attempts, control requests, tokens, attributed cost, terminal classification, final screenshot digest |
| `GET /api/v2/episodes/{id}` | none | current step, last intent status, whether the episode is open |

Rules the API enforces before any provider request:

- A screenshot must match the package's frozen dimensions and media types; the same v1 size bounds
  apply. Equality accepted, above rejected.
- `act` after a sealed failure or after `close` is rejected; the episode is over. This mirrors the
  environment rule that stepping after episode end raises.
- `act` must reference the previously issued `intent_id` and carry its result, unless it is the
  first step. A mismatched or missing reference is rejected without touching policy state. This is
  how `post_dispatch_state` receives the result it needs, and it prevents a caller from asking for
  a second action against a stale checkpoint.
- The server returns an action only after the existing PixelGym action validation accepts the
  parsed candidate. Coordinates are never clipped; an out-of-bounds candidate is a sealed
  `invalid_action`.
- Per-episode `max_steps` and `max_model_attempts_per_action` come from the package. A deployment
  also carries a human-approved total attempt cap; when reached, new episodes are refused and open
  episodes seal `cap_reached` on their next `act`.
- Responses carry only the fields listed. No raw provider text, no policy state, no expected
  answer, no diagnostics.

### Session host

A `ServingEpisodeHost` wraps the v5 runner's per-step transaction for one episode. It owns the
attempt journal, calls `build_request`, sends through the package's `ProviderTransport` under the
frozen deadline executor, persists the canonical scrubbed response before parsing, runs the pure
parser, validates the candidate, seals the intent, and on the next `act` applies
`post_dispatch_state` with the caller-supplied result before building the next request. The
checkpoint sequence is the same one the benchmark runner writes: pre-call, post-attempt,
post-parse, post-dispatch. The only difference from the runner is that dispatch is external: the
host records `intent_issued` and `result_reported` instead of `dispatch_started` and
`dispatch_committed`, and it never binds an `EnvironmentResumeRecord`, because it has no backend.

Episode state lives in a durable per-deployment session store, not in process memory. A process
restart recovers each open episode from its last durable checkpoint and either continues from a
sealed intent awaiting its result or seals `infrastructure_failure` if the last attempt's outcome
is unknown. This reuses the v5 resume rules; no new recovery semantics are introduced.

### Sandbox in the serving process

The v5 sandbox contract says policy code may reach only the allowlisted provider endpoint. The v1
serving process makes provider calls in-process, which is acceptable for a scripted demo provider
but not for a real policy package. For v5 the package's `build_request`, `reduce_state`, `parse`,
and state functions run in a policy subprocess launched under the same OS-level enforcement the v5
runner uses; the serving app process holds the journal, the transport, and the credential, and
exchanges only canonical bytes with the subprocess. This matches the existing separation in the
benchmark harness and keeps the serving app outside the sandbox.

### Evidence and operational records

Each `act` writes one immutable operational record with the existing v1 fields plus episode id,
step index, intent id, attempt identities, canonical-response digests, checkpoint digests, sealed
outcome, and reported result. Each `close` writes a terminal record. Canonical scrubbed provider
responses and checkpoints are stored as authoritative objects under the same access rule the v5
evidence contract already sets; only digests appear in operational records.

Screenshots sent to `act` are stored by digest only; their bytes are not retained. The one
exception is the final screenshot supplied to `close`: its bytes are stored as an authoritative
object, bound by digest to the terminal record, so an episode's end state can be inspected without
retaining the full trace. The same v1 size and media-type bounds apply to it, and it is stored
before the terminal record is written so a terminal record never references missing bytes.
Credentials never enter any record, and the credential-free validator runs on the task instruction
before `reset`.

### Gates for a v5 candidate

Promotion gates are per kind. A v5 gate policy reads exact episode success on a frozen
partition, cost per episode, and per-action provider latency p95 from stored v5 evaluation
evidence, with a minimum episode count. The thresholds, the partition, and whether a confidence
bound is gated are a human decision (the D4.1 analogue for this kind). The demo scripted gate
policy does not apply to v5 packages.

A demo deployment may be registered from a completed D5.6 calibration run that reached its
assigned denominator and passed the committed-evidence checks; the two published runs in the README
qualify today. Such a candidate is labelled `evidence_class: calibration` in its package and in
every serving identity response, is never described as a benchmark result, and cannot be promoted
past a demo deployment without a completed D5.9 confirmatory run. The gate report records which
evidence class it evaluated.

## Boundaries that must not move

- The observation a policy sees is one screenshot. The API adds no accessibility tree, text, step
  number, reward history, or checkpoint to the policy's input; `previous_result` feeds only the
  frozen `post_dispatch_state` reducer, exactly as it does in the benchmark runner.
- Actions are `NOOP`, bounded `CLICK`, and allowlisted `KEY`. No `DONE`, no free text, no Python.
- Success is decided by the caller's privileged evaluator. The server reports what the policy did,
  never whether it succeeded.
- Reward and termination semantics of `PixelGuiEnv` are untouched.
- No hidden retries. The frozen retry classifier is the only retry authority, and every attempt is
  recorded and counted.
- Frozen Sprint 3 and v5 calibration evidence is not reinterpreted.

## Delivery sequence

| ID | Owner | Deliverable | Stop condition |
|---|---|---|---|
| S1 | **YOU** | Approve this scope, the `/api/v2` shape, the caller-owns-the-environment decision, and sequencing relative to D5.8/D5.9 | No implementation before approval |
| S2 | **AGENT · high** | Freeze schemas: package kind v3, session API request/response contracts, operational-record and session-store schemas, registry `stateful-v5` record | Delivered: `config/stateful-policy-package.schema.json`, `config/stateful-serving.schema.json`, `pixelgym/platform/stateful_contracts.py`; interface review before S3 code |
| S3 | **AGENT · high** | `ServingEpisodeHost` over the v5 transaction with a fake policy and scripted transport; restart-recovery, sealed-failure, intent-reference, and cap tests | Stop if a v5 checkpoint or resume rule would need to change |
| S4 | **AGENT · medium** | `/api/v2` routes, bounds, error mapping, identity headers, operational records | Contract tests before provider code |
| S5 | **AGENT · high** | Policy subprocess under OS sandbox enforcement inside the serving process; credential injection at the transport only; egress-denial integration test | Stop if isolation cannot be proven without network |
| S6 | **AGENT · high** | Control-plane wiring: package verification, deploy, readiness smoke with a fake policy, rollback, and kind-aware `PolicyRuntime` | Stop if v1 behaviour changes |
| S7 | **PAIR** | Freeze the v5 gate policy and the first approved package and deployment attempt cap | Explicit approval; still no real call |
| S8 | **YOU** | Rehearse deploy, serve one fake-policy episode end to end, roll back, verify hashes; declare the gate | Agent reports raw output only |

Real provider traffic through this path needs a further explicit approval with a named package,
endpoint, and cap. It is not part of S1–S8.

## Call budgets and paid-call gates

No paid call is authorized by this document. Implementation and validation use the in-process
fake policy and the local fake provider endpoint the v5 sandbox tests already use. A future serving
deployment needs a human-approved total attempt cap; the cap is stored on the deployment record,
counted from journal records, and enforced before send.

## Test strategy

- Contract tests for every `/api/v2` endpoint, including unknown fields, wrong dimensions,
  oversized images, missing or mismatched `previous_intent_id`, and `act` after close.
- Session host tests that replay the v5 runner's existing interruption fixtures at each durable
  boundary and prove no duplicate provider call and bitwise-identical checkpoints after recovery.
- Sealed-failure tests: parse failure, invalid candidate, request failure, second qualifying retry
  error, cap reached; each produces exactly one terminal record and no further attempt.
- Sandbox integration test: the policy subprocess cannot reach a denied local listener while the
  allowed fake endpoint works.
- Control-plane tests: a `stateful-v5` package cannot be activated by the v1 runtime path, cannot
  start ready without approval, and rollback restores the previous exact version.
- Leak tests: no `act` screenshot bytes, raw provider text, policy state, credential, or expected
  answer appears in any response or operational record; the `close` screenshot is stored only as
  an authoritative object referenced by digest.
- Evidence-class tests: a calibration-class package is refused for any deployment tier other than
  demo, and every identity response carries its class.
- The fast suite stays free of network, browser, OSWorld, and real provider calls.

## Risks and limitations

- **Two owners of one episode.** The caller runs the environment and reports results; a caller
  that lies about results corrupts the policy's state but cannot affect any evidence the server
  claims, because the server records only what it was told. The API is a delivery surface, not a
  scoring surface.
- **Resume without a backend.** The v5 runner's strongest resume boundary binds backend state. The
  serving host has no backend and therefore no pre-dispatch resume record; an unknown attempt
  outcome after restart seals `infrastructure_failure`. This is stricter, not looser.
- **Sandbox cost.** A subprocess per active episode or a pooled policy worker adds latency. The
  MVP accepts one worker per deployment and serial episodes; concurrency is a later decision.
- **Alias drift.** Provider aliases that cannot pin weights remain a disclosed limitation, as in
  the benchmark.

## Alternatives considered

- **Stateless per-call serving with client-held state.** Rejected: the policy-state checkpoint is
  private and reconstructable only by the frozen reducer; handing it to callers leaks policy
  internals and breaks the redaction contract.
- **Run the environment inside the server.** Rejected for this milestone: it turns the serving
  service into a second benchmark runner and drags OSWorld into the serving process.
- **Expose the v5 runner as an RPC service and skip the platform.** Rejected: it bypasses
  approval, exact-version deployment, rollback, and immutable operational records, which is the
  point of the platform.

## Owner decisions recorded

Decided by the owner on 2026-09-09, before S1 approval of the whole note:

1. **Result reporting.** The previous step's result rides inside the next `act` call. `act`
   without a matching `previous_intent_id` and `previous_result` is rejected. The final outstanding
   result is reported to `close`.
2. **Screenshot retention.** Screenshots sent to `act` are retained by digest only. The final
   screenshot sent to `close` is retained by bytes and bound to the terminal record.
3. **Demo evidence.** A demo deployment may use a completed, evidence-checked D5.6 calibration run
   as its candidate evidence, labelled as calibration class. Promotion beyond demo requires D5.9.

## Done when

- [x] The owner explicitly approves S1 (2026-09-09).
- [x] Package kind v3, session API, session-store, and operational-record schemas are frozen and
  versioned before any host code (`pixelgym-stateful-policy-package-v1`,
  `pixelgym-serving-session-v2`, `pixelgym-serving-episode-record-v1`).
- [ ] A fake v5 policy serves a complete episode through `/api/v2` with every step recorded, and
  the same episode recovers after a process kill at every durable boundary without a duplicate
  provider call.
- [ ] Every sealed-failure class has a test that proves exactly one terminal record and no
  further attempt.
- [ ] The policy subprocess is denied unauthorized egress in a real OS-level test.
- [ ] Deploy, readiness, and rollback of a `stateful-v5` package are audited in the control store,
  and the v1 path is unchanged under its existing tests.
- [ ] The fast suite passes without network, browser, OSWorld, credentials, or model calls.
- [ ] The agent reports raw gate commands, exit statuses, counts, and full output without declaring
  the human gate passed.
