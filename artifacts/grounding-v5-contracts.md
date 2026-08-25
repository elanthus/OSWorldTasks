# PixelGym v5 implementation contracts

**Status:** implemented for no-cost D5.2–D5.5 validation; awaiting human interface,
security-boundary, backend-resume, and development-sample review

**Primary reader:** the project owner reviewing the v5 implementation before any calibration
policy or provider call is approved

## Outcome

V5 is implemented as the separate `pixelgym.grounding.v5` package. It does not change the
`PixelGuiEnv` observation, action, reward, termination, truncation, or `info` contracts. The
implementation generates all development, calibration, and confirmatory task specifications
without provider access. It runs scripted stateful policies through the normal environment boundary
and stores action execution as durable, content-bound evidence.

This note freezes the review surface. It does not approve D5.6, a provider, a policy panel, a paid
call cap, calibration, confirmatory evaluation, D4.12, D5.10, or public wording.

## Frozen identities

| Contract | Version or source | Identity rule |
|---|---|---|
| Protocol | `pixelgym-agent-v5` | New protocol; no earlier result is rescored |
| Generator | `pixelgym-agent-v5-generator-v1` | Canonical task bytes plus explicit seed record |
| Task | `pixelgym-agent-v5-task-v1` | `v5-` plus the first 24 hexadecimal characters of the canonical identity hash |
| Policy | `pixelgym-agent-v5-policy-v1` | `policy-` plus the first 20 hexadecimal characters of every manifest identity field |
| Attempt | `pixelgym-agent-v5-attempt-v1` | Trial ID, zero-based environment step, and zero-based model attempt |
| Redaction | `pixelgym-agent-v5-redaction-v1` | Canonical authoritative digest, derivative digest, and policy version |
| Action | `pixelgym-action-v1` | Existing four-field mapping and key-allowlist version |

The packaged JSON schemas are in [`pixelgym/grounding/v5/schemas`](../pixelgym/grounding/v5/schemas/).
The complete Python contracts are in
[`pixelgym/grounding/v5/contracts.py`](../pixelgym/grounding/v5/contracts.py).

## Dataset freeze

[`pixelgym/grounding/v5/seeds.py`](../pixelgym/grounding/v5/seeds.py) contains explicit, disjoint
seed lists and canonical parameter records:

- development: 24 episodes, four per family;
- calibration: 60 episodes, ten per family, including twelve logical robustness pairs; and
- confirmatory: 96 episodes, sixteen per family, including twenty-four logical robustness pairs.

Robustness twins share a semantic digest and differ in wording and control order. The generator
binds the variant, task values, target transitions, action limit, and expected terminal result into
the task identity. [`partition_manifest`](../pixelgym/grounding/v5/manifests.py) produces the
content-bound seed, task, and semantic index for a partition.

## Policy and environment boundary

The policy receives the task instruction at `reset` and one 1024×768 RGB screenshot at each `act`.
It does not receive a seed, target, box, DOM, accessibility tree, action history, step number,
diagnostic, partial score, or expected result. The generated public task record excludes those
fields. The capture-only browser exposes control boxes and transition state to its integration
harness; the stateful evaluation runner does not import or read that instrumentation.

The sandbox manifest binds one credential-free provider origin and denies external search,
browser/DOM handles, shell access, shared storage, inbound listeners, and cross-policy channels.
Provider SDK and proxy retries are disabled in the policy identity. The journaled runner is the only
request and retry authority. The no-cost operating-system enforcement probe currently uses Darwin
`sandbox-exec`; this platform limitation must remain disclosed if another host runs calibration.

## Durable action sequence

Each action follows this stored sequence:

```mermaid
flowchart LR
    A[attempt_started] --> B[canonical response bytes]
    B --> C[terminal attempt checkpoint]
    C --> D[parsed_action_candidate]
    D --> E[validated sealed intent + environment resume record]
    E --> F[dispatch_started]
    F --> G[backend action exactly once]
    G --> H[dispatch_committed + post-dispatch checkpoint]
```

The provider request starts only after `attempt_started` commits. The runner stores the scrubbed
canonical response before the policy reducer or parser receives it. A pure reducer derives the next
policy checkpoint from those exact bytes. Validation reads only the durable candidate. The runner
stores `dispatch_started` before it invokes the backend.

Recovery never issues a second model request for an attempt with an unknown post-send outcome.
Before dispatch, it restores and verifies the task ID, backend identity, step count, screenshot,
application state, and checkpoint digest. A `dispatch_started` record without
`dispatch_committed` fails as infrastructure evidence; the runner does not redispatch it.

## Backend resume behavior

`V5FakeBackend` and the core `FakeBackend` provide content-addressed checkpoint and restore. Tests
restore into a new backend instance and compare the bound task, step, application state, screenshot,
and checkpoint bytes before dispatch.

`OSWorldBackend` provides a live-session reconnect record because the pinned OSWorld provider does
not expose a content-addressed per-action VM snapshot. Reconnect verifies the exact task, live
backend identity, structured-action count, privileged application-state digest, and screenshot
digest. If the live session no longer exists or any binding differs, recovery fails closed. This is
not a process-independent OSWorld snapshot and must not be described as one.

## Evidence and secrets

Credential validation runs before task reset or screenshot creation. It rejects credential-shaped
field names and values, runtime secret handles, URL userinfo, and credential-shaped URL parameters
without logging the candidate value. Provider credentials may enter only the transport after
canonical request bytes are sealed; the no-cost implementation has no credential input.

The authoritative store retains replay-required objects, including launch values such as
`app_url`. The deterministic publishable transform removes hostnames, usernames, account IDs,
provider-private fields, policy state, application URLs, and endpoints. A relation record binds the
authoritative digest, publishable digest, and redaction version. Integrity reporting rereads every
referenced byte and verifies its size and digest.

## Review and stop conditions

Before D5.6, the owner should review:

1. task samples from all six families for screenshot-only solvability and instruction sufficiency;
2. the public-task and policy-sandbox boundary for target or credential leakage;
3. the interruption matrix and the fail-closed `dispatch_started` behavior;
4. the OSWorld live-reconnect limitation and required integration evidence; and
5. the plan-only environment-action, model-attempt, control-request, and wire-request caps for each
   proposed policy.

No real provider call is part of the implementation or its automated tests. D5.6 remains the next
human-owned stop.
